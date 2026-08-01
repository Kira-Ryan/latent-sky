<script lang="ts">
  import { sky } from "../state/store.svelte";

  let canvas = $state<HTMLCanvasElement | null>(null);

  // Canvas-rendered from the SAME LUT png the encoder indexed (§7.2a) — the
  // legend cannot drift from the imagery because they share one file.
  $effect(() => {
    const layer = sky.activeLayer;
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
        // Dark underlay so the ramp's alpha tail reads as it does over the globe.
        ctx.fillStyle = "#0b0e14";
        ctx.fillRect(0, 0, target.width, target.height);
        ctx.drawImage(image, 0, 0, target.width, target.height);
      })
      .catch((err: unknown) => {
        console.error(`legend: failed to draw LUT ${layer.lutUrl}`, err);
        throw err;
      });
  });

  const label = $derived(sky.activeLayer?.label ?? "");
  const units = $derived(sky.activeLayer?.units ?? "");
  const vmin = $derived(sky.activeLayer?.vmin ?? 0);
  const vmax = $derived(sky.activeLayer?.vmax ?? 1);
</script>

{#if sky.activeLayer}
  <figure
    class="legend"
    role="img"
    aria-label={`Colour scale for ${label}: ${vmin} to ${vmax} ${units}`}
  >
    <canvas bind:this={canvas} width="256" height="10"></canvas>
    <figcaption>
      <span class="bound">{vmin}</span>
      <span class="title">{label} · {units}</span>
      <span class="bound">{vmax}</span>
    </figcaption>
  </figure>
{/if}

<style>
  .legend {
    margin: 0;
    flex: none;
    width: 256px;
  }

  canvas {
    display: block;
    width: 100%;
    height: 10px;
    border-radius: 2px;
    border: 1px solid var(--panel-border);
  }

  figcaption {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 8px;
    margin-top: 3px;
    font-size: 11px;
    color: var(--text-dim);
  }

  .title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .bound {
    font-variant-numeric: tabular-nums;
  }
</style>
