<script lang="ts">
  import { onMount } from "svelte";
  import { sky } from "../state/store.svelte";
  import { createGlobe, type GlobeApi, type HeroAnchor } from "../globe";
  import { heroFrameFor, loadManifest, stormNameFor, type Variable } from "../data/manifest";
  import { ageLabel, formatUtc } from "../data/time";
  import { writeEventToUrl } from "../data/catalogue";
  import { motionOk } from "../motion";
  import TimeScrubber from "./TimeScrubber.svelte";
  import RevealSlider from "./RevealSlider.svelte";
  import Legend from "./Legend.svelte";
  import VariablePicker from "./VariablePicker.svelte";
  import Caveat from "./Caveat.svelte";
  import PlaceLabel from "./PlaceLabel.svelte";
  import EventSwitcher from "./EventSwitcher.svelte";

  let globeEl: HTMLDivElement;
  /**
   * The long-lived façade: one CesiumWidget for the page, with the per-manifest
   * session swapped underneath it on every event switch (see globe/index.ts).
   */
  let globe: GlobeApi | null = null;
  /**
   * Mirrors `globe` only while a session is actually live — null during boot
   * and for the duration of a switch, so every piece of hero chrome stands down
   * rather than pointing at data that is being replaced.
   */
  let api = $state<GlobeApi | null>(null);
  let bootError = $state<string | null>(null);
  let anchor = $state<HeroAnchor | null>(null);
  // The hero-pointing overlay (invitation, marker, place label). Hidden only
  // by the __latentSky.setHeroOverlay hook — the OG capture needs a shot with
  // no hero affordances (web/tests/capture-og.mjs).
  let heroOverlayVisible = $state(true);

  // The invitation names the storm honestly (§8.4 — copy must match the data):
  // the manifest's first-class run.stormName, falling back through
  // stormNameFor()'s caption heuristic. No storm named, no claim made.
  const stormName = $derived(sky.manifest ? stormNameFor(sky.manifest) : "the hero region");

  /**
   * The issue line under the wordmark. `nowMs` ticks because $derived reacts to
   * state, not to the passage of time: without it a page left open all evening
   * would still claim the forecast was issued "1 h ago". A minute is finer than
   * the whole-hour resolution the label prints, so the number is never stale.
   */
  let nowMs = $state(Date.now());
  $effect(() => {
    const id = setInterval(() => (nowMs = Date.now()), 60_000);
    return () => clearInterval(id);
  });
  const issuedText = $derived(
    sky.manifest?.run.init ? `Forecast issued ${formatUtc(sky.manifest.run.init)}` : "",
  );
  const issuedAge = $derived(ageLabel(sky.manifest?.run.init, nowMs));

  /**
   * Point the globe at the manifest currently in the store. This is the whole
   * of "switching must fully re-initialise the globe" on the UI side: the store
   * has already been reset by sky.init(), globe.load() disposes the previous
   * session and builds a fresh one, and `api` goes null across the gap so no
   * $effect can push stale state into either.
   */
  async function activate(): Promise<void> {
    const manifest = sky.manifest;
    if (!manifest) throw new Error("store has no manifest to activate");
    if (!globe) throw new Error("activate() before the globe was created");
    api = null;
    anchor = null;
    await globe.load(manifest);
    api = globe;
  }

  /**
   * Switch to another catalogue event. The manifest is fetched and validated
   * BEFORE anything is torn down, so a bad manifest leaves the current event on
   * screen rather than emptying the globe.
   */
  async function selectEvent(id: string): Promise<void> {
    const catalogue = sky.catalogue;
    if (!catalogue || sky.switching || id === sky.activeEventId) return;
    const target = catalogue.events.find((e) => e.id === id);
    if (!target) throw new Error(`no catalogue event with id ${JSON.stringify(id)}`);

    sky.switching = true;
    try {
      const manifest = await loadManifest(target.manifestUrl);
      sky.activeEventId = target.id;
      writeEventToUrl(catalogue, target.id); // the address bar is the shareable link
      sky.init(manifest); // full view reset — variable, time, wipe, camera state
      await activate();
    } catch (err: unknown) {
      bootError = err instanceof Error ? (err.stack ?? err.message) : String(err);
      throw err; // surface loudly — the on-screen panel is not a substitute
    } finally {
      sky.switching = false;
    }
  }

  function enterStorm(): void {
    // heroAvailable guard: the test hook exposes this unconditionally, and a
    // hero-free manifest has nowhere to fly — a silent no-op, never a throw.
    if (!api || !api.heroAvailable || sky.flying || sky.view !== "orbit") return;
    api.setIdleSpin(false);
    // Arrive on the most developed frame — run.heroFrame when the manifest
    // declares one, else the last frame: frames are chronological, and quiet
    // early frames must never sit under the "enter the storm" copy.
    sky.playing = false;
    sky.frame = sky.manifest ? heroFrameFor(sky.manifest) : Math.max(sky.frameCount - 1, 0);
    sky.view = "hero";
    sky.flying = true;
    api.flyToHero((completed) => {
      sky.flying = false;
      // One auto-sweep on arrival, then settle mid (§8.4). A cancelled flight
      // (viewer grabbed the globe) skips the sweep — they took the controls.
      if (completed) sky.sweepPending = true;
    });
  }

  function returnToOrbit(): void {
    if (!api || sky.flying || sky.view !== "hero") return;
    sky.view = "orbit";
    sky.flying = true;
    api.flyToOrbit(() => {
      sky.flying = false;
    });
  }

  function onWindowKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape" && sky.view === "hero" && !sky.flying) {
      returnToOrbit();
    }
  }

  onMount(() => {
    let disposed = false;
    let created: GlobeApi | null = null;
    let removeAnchor: (() => void) | null = null;
    let removeFrame: (() => void) | null = null;
    const stopSpin = (): void => created?.setIdleSpin(false);
    const spinStopEvents = ["pointerdown", "wheel", "keydown", "touchstart"] as const;
    (async () => {
      if (!sky.manifest) throw new Error("store not initialised before App mount");
      const pixelReadback = new URLSearchParams(location.search).has("test");
      created = await createGlobe(globeEl, { pixelReadback });
      if (disposed) {
        created.destroy();
        return;
      }
      globe = created;
      // Subscriptions live on the façade, not on the session, so they survive
      // every event switch and are registered exactly once.
      removeFrame = created.onFrame((f) => {
        sky.frame = f;
      });
      removeAnchor = created.onAnchor((a) => {
        anchor = a;
      });

      // Test hook: the smoke test drives the app through the same store the UI
      // uses. Getters throughout — a switch changes frame count, variables and
      // hero availability, and a snapshot taken at mount would lie afterwards.
      (window as unknown as Record<string, unknown>).__latentSky = {
        get ready(): boolean {
          return api !== null;
        },
        get frameCount(): number {
          return globe?.frameCount ?? 0;
        },
        get variables(): Variable[] {
          return [...sky.variables];
        },
        get heroAvailable(): boolean {
          return sky.heroAvailable;
        },
        get switching(): boolean {
          return sky.switching;
        },
        get activeEventId(): string | null {
          return sky.activeEventId;
        },
        get events(): { id: string; title: string; subtitle: string | null }[] {
          return sky.events.map((e) => ({
            id: e.id,
            title: e.title,
            subtitle: e.subtitle ?? null,
          }));
        },
        switchEvent: (id: string) => selectEvent(id),
        setFrame: (f: number) => {
          sky.playing = false;
          sky.frame = f;
          globe?.setFrame(f);
        },
        setVariable: (v: Variable) => {
          sky.variable = v;
        },
        setSplit: (x: number) => {
          sky.split = x;
        },
        enterStorm: () => enterStorm(),
        returnToOrbit: () => returnToOrbit(),
        // OG capture (licence-critical): hides the invitation, hero marker and
        // place label so a global-only shot carries no hero affordances.
        setHeroOverlay: (visible: boolean) => {
          heroOverlayVisible = visible;
        },
        getView: () => ({ view: sky.view, flying: sky.flying, split: sky.split }),
        requestRender: () => globe?.requestRender(),
      };

      await activate();

      // The arrival (§8.1): the planet is already slowly turning. First user
      // interaction of any kind stops it for good — motion is the loading
      // state, not a screensaver. No spin under prefers-reduced-motion
      // (setIdleSpin is gated through motionOk in flight.ts). Deliberately NOT
      // restarted after an event switch: choosing an event IS taking the
      // controls, and these listeners have already fired by then.
      created.setIdleSpin(true);
      for (const type of spinStopEvents) {
        window.addEventListener(type, stopSpin, { once: true, passive: true });
      }
    })().catch((err: unknown) => {
      bootError = err instanceof Error ? (err.stack ?? err.message) : String(err);
      throw err; // surface loudly — the on-screen panel is not a substitute
    });
    return () => {
      disposed = true;
      for (const type of spinStopEvents) window.removeEventListener(type, stopSpin);
      removeAnchor?.();
      removeFrame?.();
      created?.destroy();
      globe = null;
      api = null;
    };
  });

  // Svelte never touches Cesium objects — these setters are the whole
  // conversation between UI state and the globe (§6.4).
  $effect(() => {
    api?.setVariable(sky.variable);
  });
  $effect(() => {
    api?.setFrame(sky.frame);
  });
  $effect(() => {
    api?.setPlaying(sky.playing);
  });
  $effect(() => {
    api?.setSpeed(sky.speed);
  });
  $effect(() => {
    api?.setSplit(sky.split);
  });
  $effect(() => {
    api?.setShowFine(sky.showFine);
  });
  $effect(() => {
    api?.setReveal(sky.revealEngaged);
  });

  // Hero chrome keys off the LOADED MANIFEST's layers (sky.heroAvailable), not
  // the catalogue's hasHero hint and not a snapshot taken at mount — a switch
  // must make the invitation appear or disappear on the strength of the data
  // that is actually on the globe.
  const inviteVisible = $derived(
    api !== null && sky.heroAvailable && heroOverlayVisible && sky.view === "orbit" && !sky.flying,
  );
  const inviteAnchored = $derived(inviteVisible && anchor !== null && anchor.visible);

  // Hero-free manifests (the public pre-forecast state): a quiet strip where
  // the invitation would dock, promising what arrives with the first forecast
  // run. Built-in copy — the schema's run object is closed
  // (additionalProperties: false) and no existing field carries this; revisit
  // if the schema ever grows a pre-forecast note.
  const comingVisible = $derived(api !== null && !sky.heroAvailable && heroOverlayVisible);
</script>

<svelte:window onkeydown={onWindowKeydown} />

<div class="app">
  <div class="globe-wrap">
    <div class="globe" bind:this={globeEl}></div>
    <RevealSlider />

    <!-- The masthead carries the wordmark and, when the catalogue offers a
         choice, the event switcher. The switcher sits on the wordmark's line so
         the masthead's height is unchanged whether or not it renders — the
         "Return to orbit" button docks directly beneath it. -->
    <header class="masthead">
      <div class="masthead-row">
        <h1>Latent Sky</h1>
        <EventSwitcher onselect={selectEvent} />
      </div>
      <!-- The line under the wordmark used to spend itself on a slug. It now says
           when the forecast was issued, which is the one fact that separates a
           live run from a case study, and the age when that age still informs.
           No badge and no "live": a number that grows is honest on a cached page
           where a coloured dot would not be. -->
      <p class="runid" title={sky.manifest?.run.id}>
        {#if sky.switching}
          loading…
        {:else if issuedText}
          {issuedText}{#if issuedAge}<span class="age">{" · "}{issuedAge}</span>{/if}
        {:else}
          {sky.manifest?.run.id}
        {/if}
      </p>
    </header>

    <!-- One clean top-right stack: variable pills, then the reveal toggle,
         then the legend — right-aligned with one consistent gap, so nothing
         in this corner can collide or clip at common widths. -->
    <div
      class="corner-stack"
      style:--masthead-reserve={sky.showSwitcher ? "420px" : "220px"}
    >
      <VariablePicker />
      <Legend />
    </div>

    <!-- The invitation (§8.4): pinned over the hero region while it is on the
         near side of the planet; docked low-centre when the spin has carried
         it behind the limb, so the way in is never lost. Amber is the hero
         accent — reserved for the generative/hero moments. -->
    {#if inviteVisible}
      {#if inviteAnchored && anchor}
        <span
          class="hero-marker"
          style:left={`${anchor.x}px`}
          style:top={`${anchor.y}px`}
          aria-hidden="true"
        ></span>
      {/if}
      <button
        class="invite"
        class:anchored={inviteAnchored}
        style:left={inviteAnchored && anchor ? `${anchor.x}px` : undefined}
        style:top={inviteAnchored && anchor ? `${anchor.y + 26}px` : undefined}
        onclick={enterStorm}
      >
        <span class="invite-storm">{stormName}</span>
        <span class="invite-sep" aria-hidden="true">·</span>
        <span class="invite-action">enter the storm</span>
      </button>
    {/if}

    <!-- The pre-forecast strip: hero-free manifests keep the orbit experience
         whole, and this quiet line says why there is no way down yet. -->
    {#if comingVisible}
      <p class="coming">
        Global view: 0.5° reanalysis · kilometre-scale AI detail arrives with the first forecast run
      </p>
    {/if}

    <!-- The place label ("is that Vietnam?" — real demo feedback): quiet,
         always present in both stable views, pinned to the hero rectangle. -->
    <PlaceLabel
      {anchor}
      visible={api !== null && sky.heroAvailable && heroOverlayVisible}
    />

    {#if sky.view === "hero" && !sky.flying}
      <!-- Docks under the masthead. The switcher sits on the wordmark's line
           and makes that block 6px taller, so the offset follows it rather
           than being a constant that silently starts colliding. -->
      <button
        class="return"
        style:top={sky.showSwitcher ? "70px" : undefined}
        onclick={returnToOrbit}
        title="Esc"
      >
        <svg viewBox="0 0 14 14" width="12" height="12" aria-hidden="true">
          <circle cx="7" cy="7" r="5.4" fill="none" stroke="currentColor" stroke-width="1.3" />
          <ellipse cx="7" cy="7" rx="9" ry="3.2" fill="none" stroke="currentColor" stroke-width="1"
            transform="rotate(-18 7 7)" />
        </svg>
        Return to orbit
      </button>
    {/if}

    {#if bootError}
      <pre class="boot-error overlay-error">{bootError}</pre>
    {/if}
  </div>
  <footer class="hud">
    <TimeScrubber />
    <Caveat />
  </footer>
</div>

<style>
  .app {
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  .globe-wrap {
    position: relative;
    flex: 1;
    min-height: 0;
  }

  .globe {
    position: absolute;
    inset: 0;
  }

  .masthead {
    position: absolute;
    top: 16px;
    left: 20px;
    z-index: 4;
    pointer-events: none;
  }

  /* One row: wordmark, then the event switcher when the catalogue holds more
     than one event. The switcher renders nothing at all otherwise, and the row
     collapses to exactly the wordmark it always was. */
  .masthead-row {
    display: flex;
    align-items: center;
    gap: 14px;
  }

  /* Title treatment: letterspaced, quiet — an instrument's nameplate, not a
     billboard. The wordmark carries the piece's only gradient. */
  .masthead h1 {
    margin: 0;
    font-size: 15px;
    font-weight: 500;
    letter-spacing: 0.34em;
    text-transform: uppercase;
    color: var(--text);
    text-shadow: 0 1px 8px rgba(4, 8, 18, 0.8);
  }

  .runid {
    margin: 3px 0 0;
    font-size: 10.5px;
    letter-spacing: 0.08em;
    color: var(--text-faint);
    font-variant-numeric: tabular-nums;
  }

  /* The age is the part that moves, so it is the part that recedes: the
     absolute instant is what the reader should trust and remember. */
  .runid .age {
    opacity: 0.75;
  }

  .corner-stack {
    position: absolute;
    top: 14px;
    right: 18px;
    z-index: 4;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 10px;
    /* Never intrude on the masthead at 1024-class widths: cap the stack's
       width and let the picker wrap its pills instead of overlapping. The
       reserve is set inline, because the masthead is wider when the event
       switcher renders — 220px for the wordmark alone, 420px with the pill. */
    /* Floored at the legend's own 256px so a bigger reserve can never squeeze
       the stack narrower than the one element inside it that cannot shrink. */
    max-width: min(420px, max(256px, calc(100vw - var(--masthead-reserve, 220px))));
  }

  /* ——— the invitation ——— */

  .hero-marker {
    position: absolute;
    z-index: 4;
    width: 7px;
    height: 7px;
    margin: -3.5px 0 0 -3.5px;
    border-radius: 50%;
    background: var(--amber);
    box-shadow: 0 0 0 3px rgba(251, 191, 36, 0.18), 0 0 14px 2px rgba(251, 191, 36, 0.35);
    pointer-events: none;
  }

  .invite {
    position: absolute;
    z-index: 5;
    display: inline-flex;
    align-items: baseline;
    gap: 7px;
    padding: 8px 16px;
    font-size: 12.5px;
    letter-spacing: 0.04em;
    white-space: nowrap;
    color: var(--text);
    background: var(--panel);
    border: 1px solid rgba(251, 191, 36, 0.4);
    border-radius: 999px;
    backdrop-filter: blur(6px);
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.45);
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
  }

  .invite:hover,
  .invite:focus-visible {
    border-color: rgba(251, 191, 36, 0.85);
    box-shadow: 0 6px 28px rgba(251, 191, 36, 0.12), 0 6px 24px rgba(0, 0, 0, 0.45);
  }

  .invite.anchored {
    transform: translateX(-50%);
  }

  .invite:not(.anchored) {
    left: 50%;
    bottom: 26px;
    transform: translateX(-50%);
  }

  .invite-storm {
    color: var(--amber);
    font-weight: 600;
  }

  .invite-sep {
    color: var(--text-faint);
  }

  .invite-action {
    color: var(--text-dim);
    text-transform: uppercase;
    font-size: 10.5px;
    letter-spacing: 0.18em;
  }

  /* ——— the pre-forecast strip (hero-free manifests) ——— */

  .coming {
    position: absolute;
    z-index: 4;
    left: 50%;
    bottom: 26px;
    transform: translateX(-50%);
    margin: 0;
    padding: 7px 16px;
    font-size: 11.5px;
    letter-spacing: 0.06em;
    white-space: nowrap;
    max-width: calc(100vw - 32px);
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-dim);
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 999px;
    backdrop-filter: blur(6px);
    pointer-events: none;
  }

  /* ——— return to orbit ——— */

  .return {
    position: absolute;
    top: 64px;
    left: 20px;
    z-index: 4;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 6px 13px;
    font-size: 12px;
    letter-spacing: 0.05em;
    color: var(--text-dim);
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 999px;
    backdrop-filter: blur(6px);
    transition: color 0.2s ease, border-color 0.2s ease;
  }

  .return:hover,
  .return:focus-visible {
    color: var(--accent);
    border-color: rgba(79, 209, 197, 0.5);
  }

  .overlay-error {
    position: absolute;
    top: 20%;
    left: 50%;
    transform: translateX(-50%);
    z-index: 10;
    max-height: 60%;
    overflow: auto;
  }

  .hud {
    flex: none;
    background: var(--panel-solid);
    border-top: 1px solid var(--panel-border);
    padding: 10px 18px 8px;
  }
</style>
