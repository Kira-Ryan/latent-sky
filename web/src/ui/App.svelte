<script lang="ts">
  import { onMount } from "svelte";
  import { sky } from "../state/store.svelte";
  import { createGlobe, type GlobeApi } from "../globe";
  import type { Variable } from "../data/manifest";
  import TimeScrubber from "./TimeScrubber.svelte";
  import RevealSlider from "./RevealSlider.svelte";
  import Legend from "./Legend.svelte";
  import VariablePicker from "./VariablePicker.svelte";
  import Caveat from "./Caveat.svelte";

  let globeEl: HTMLDivElement;
  let api = $state<GlobeApi | null>(null);
  let bootError = $state<string | null>(null);

  onMount(() => {
    let disposed = false;
    let created: GlobeApi | null = null;
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
      api = created;
      // Test hook: the smoke test drives the app through the same store the UI uses.
      (window as unknown as Record<string, unknown>).__latentSky = {
        ready: true,
        frameCount: created.frameCount,
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
        requestRender: () => created!.requestRender(),
      };
    })().catch((err: unknown) => {
      bootError = err instanceof Error ? (err.stack ?? err.message) : String(err);
      throw err; // surface loudly — the on-screen panel is not a substitute
    });
    return () => {
      disposed = true;
      created?.destroy();
      api = null;
    };
  });

  // Svelte never touches Cesium objects — these six setters are the whole
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
</script>

<div class="app">
  <div class="globe-wrap">
    <div class="globe" bind:this={globeEl}></div>
    <RevealSlider />
    <header class="masthead">
      <h1>Latent Sky</h1>
      <p class="runid">{sky.manifest?.run.id}</p>
    </header>
    <div class="picker-dock">
      <VariablePicker />
    </div>
    {#if bootError}
      <pre class="boot-error overlay-error">{bootError}</pre>
    {/if}
  </div>
  <footer class="hud">
    <div class="hud-row">
      <TimeScrubber />
      <Legend />
    </div>
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
    top: 14px;
    left: 18px;
    z-index: 4;
    pointer-events: none;
  }

  .masthead h1 {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text);
  }

  .runid {
    margin: 2px 0 0;
    font-size: 11px;
    color: var(--text-dim);
  }

  .picker-dock {
    position: absolute;
    top: 14px;
    right: 18px;
    z-index: 4;
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
    background: var(--panel);
    border-top: 1px solid var(--panel-border);
    padding: 10px 18px 8px;
  }

  .hud-row {
    display: flex;
    align-items: center;
    gap: 24px;
    flex-wrap: wrap;
  }
</style>
