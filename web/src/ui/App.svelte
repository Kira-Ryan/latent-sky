<script lang="ts">
  import { onMount } from "svelte";
  import { sky } from "../state/store.svelte";
  import { createGlobe, type GlobeApi, type HeroAnchor } from "../globe";
  import type { Variable } from "../data/manifest";
  import { motionOk } from "../motion";
  import TimeScrubber from "./TimeScrubber.svelte";
  import RevealSlider from "./RevealSlider.svelte";
  import Legend from "./Legend.svelte";
  import VariablePicker from "./VariablePicker.svelte";
  import Caveat from "./Caveat.svelte";

  let globeEl: HTMLDivElement;
  let api = $state<GlobeApi | null>(null);
  let bootError = $state<string | null>(null);
  let anchor = $state<HeroAnchor | null>(null);

  // The invitation names the storm honestly, from the manifest's own caption
  // (§8.4 — copy must match the data). The dev sample's note names Chanthu;
  // a Gaemi forecast run's note will name Gaemi. No storm named, no claim made.
  const stormName = $derived(
    /Typhoon\s+[A-Z][a-z]+/.exec(sky.manifest?.run.generatedNote ?? "")?.[0] ?? "the hero region",
  );

  function enterStorm(): void {
    if (!api || sky.flying || sky.view !== "orbit") return;
    api.setIdleSpin(false);
    // Arrive on the most developed frame — the frames are chronological, and
    // quiet early frames must never sit under the "enter the storm" copy.
    sky.playing = false;
    sky.frame = Math.max(sky.frameCount - 1, 0);
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
    const stopSpin = (): void => created?.setIdleSpin(false);
    const spinStopEvents = ["pointerdown", "wheel", "keydown", "touchstart"] as const;
    (async () => {
      const manifest = sky.manifest;
      if (!manifest) throw new Error("store not initialised before App mount");
      const pixelReadback = new URLSearchParams(location.search).has("test");
      created = await createGlobe(globeEl, manifest, { pixelReadback });
      if (disposed) {
        created.destroy();
        return;
      }
      created.onFrame((f) => {
        sky.frame = f;
      });
      removeAnchor = created.onAnchor((a) => {
        anchor = a;
      });
      // The arrival (§8.1): the planet is already slowly turning. First user
      // interaction of any kind stops it for good — motion is the loading
      // state, not a screensaver. No spin under prefers-reduced-motion
      // (setIdleSpin is gated through motionOk in flight.ts).
      created.setIdleSpin(true);
      for (const type of spinStopEvents) {
        window.addEventListener(type, stopSpin, { once: true, passive: true });
      }
      api = created;
      // Test hook: the smoke test drives the app through the same store the UI uses.
      (window as unknown as Record<string, unknown>).__latentSky = {
        ready: true,
        frameCount: created.frameCount,
        variables: [...sky.variables],
        setFrame: (f: number) => {
          sky.playing = false;
          sky.frame = f;
          created!.setFrame(f);
        },
        setVariable: (v: Variable) => {
          sky.variable = v;
        },
        setSplit: (x: number) => {
          sky.split = x;
        },
        enterStorm: () => enterStorm(),
        returnToOrbit: () => returnToOrbit(),
        getView: () => ({ view: sky.view, flying: sky.flying, split: sky.split }),
        requestRender: () => created!.requestRender(),
      };
    })().catch((err: unknown) => {
      bootError = err instanceof Error ? (err.stack ?? err.message) : String(err);
      throw err; // surface loudly — the on-screen panel is not a substitute
    });
    return () => {
      disposed = true;
      for (const type of spinStopEvents) window.removeEventListener(type, stopSpin);
      removeAnchor?.();
      created?.destroy();
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

  const inviteVisible = $derived(
    api !== null && api.heroAvailable && sky.view === "orbit" && !sky.flying,
  );
  const inviteAnchored = $derived(inviteVisible && anchor !== null && anchor.visible);
</script>

<svelte:window onkeydown={onWindowKeydown} />

<div class="app">
  <div class="globe-wrap">
    <div class="globe" bind:this={globeEl}></div>
    <RevealSlider />

    <header class="masthead">
      <h1>Latent Sky</h1>
      <p class="runid">{sky.manifest?.run.id}</p>
    </header>

    <!-- One clean top-right stack: variable pills, then the reveal toggle,
         then the legend — right-aligned with one consistent gap, so nothing
         in this corner can collide or clip at common widths. -->
    <div class="corner-stack">
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

    {#if sky.view === "hero" && !sky.flying}
      <button class="return" onclick={returnToOrbit} title="Esc">
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
       width and let the picker wrap its pills instead of overlapping. */
    max-width: min(420px, calc(100vw - 220px));
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
