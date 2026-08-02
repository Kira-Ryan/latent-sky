<script lang="ts">
  import { sky } from "../state/store.svelte";

  let canvas = $state<HTMLCanvasElement | null>(null);

  // Canvas-rendered from the SAME LUT png the encoder indexed (§7.2a) — the
  // legend cannot drift from the imagery because they share one file.
  $effect(() => {
    const layer = sky.legendLayer;
    const target = canvas;
    if (!layer || !target) return;
    const image = new Image();
    image.src = layer.lutUrl;
    image
      .decode()
      .then(() => {
        const ctx = target.getContext("2d");
        if (!ctx) throw new Error("legend: no 2d context");
        ctx.imageSmoothingEnabled = false;
        // Dark underlay so the ramp's alpha tail reads as it does over the globe
        // (the basemap's ocean colour — one palette, one truth).
        ctx.fillStyle = "#070c1a";
        ctx.fillRect(0, 0, target.width, target.height);
        ctx.drawImage(image, 0, 0, target.width, target.height);
      })
      .catch((err: unknown) => {
        console.error(`legend: failed to draw LUT ${layer.lutUrl}`, err);
        throw err;
      });
  });

  const label = $derived(sky.legendLayer?.label ?? "");
  const units = $derived(sky.legendLayer?.units ?? "");
  const vmin = $derived(sky.legendLayer?.vmin ?? 0);
  const vmax = $derived(sky.legendLayer?.vmax ?? 1);
</script>

{#if sky.legendLayer}
  <figure
    class="legend"
    role="img"
    aria-label={`Colour scale for ${label}: ${vmin} to ${vmax} ${units}`}
  >
    <!-- Title on its own line, wrapping rather than clipping — long labels
         ("10 m wind speed — generated, ~2 km") must never truncate. -->
    <figcaption class="title">{label} · {units}</figcaption>
    <canvas bind:this={canvas} width="256" height="10"></canvas>
    <div class="bounds" aria-hidden="true">
      <span class="bound">{vmin}</span>
      <span class="bound">{vmax}</span>
    </div>
  </figure>
{/if}

<style>
  .legend {
    margin: 0;
    flex: none;
    width: 256px;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 8px;
    padding: 8px 10px 7px;
    backdrop-filter: blur(6px);
  }

  .title {
    margin: 0 0 6px;
    font-size: 11px;
    line-height: 1.35;
    letter-spacing: 0.03em;
    color: var(--text-dim);
    overflow-wrap: anywhere;
  }

  canvas {
    display: block;
    width: 100%;
    height: 10px;
    border-radius: 2px;
    border: 1px solid var(--panel-border);
  }

  .bounds {
    display: flex;
    justify-content: space-between;
    margin-top: 3px;
    font-size: 11px;
    color: var(--text-dim);
  }

  .bound {
    font-variant-numeric: tabular-nums;
  }
</style>
