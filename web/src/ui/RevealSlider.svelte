<script lang="ts">
  import { onDestroy } from "svelte";
  import { sky } from "../state/store.svelte";

  let wrapEl = $state<HTMLDivElement | null>(null);
  let sweeping = $state(false);
  let rafId = 0;

  // Divider-pinned labels — the comparison must explain itself at the curtain
  // (first-viewer test, Architecture.md §6.3). Wording is honest per run kind:
  // in the dev sample the sharp side is real CWA/WRF analysis, NOT AI output.
  const coarsePill = "≈ 25 km · model input";
  const finePill = $derived(
    sky.manifest?.run.kind === "forecast"
      ? "≈ 2 km · AI generated"
      : "≈ 2 km · real analysis — dev stand-in",
  );

  // Fade a pill out as its side collapses to a sliver it no longer describes.
  const leftPillVisible = $derived(sky.split > 0.14);
  const rightPillVisible = $derived(sky.split < 0.86);

  function startSweep(): void {
    sweeping = true;
    const period = 7000; // ms for a full there-and-back
    const t0 = performance.now();
    const loop = (t: number): void => {
      if (!sweeping) return;
      const elapsed = t - t0;
      // One full pass each way, then settle mid-wipe: the resting state IS the
      // comparison, so the sweep must never end on a single-layer view (§6.3).
      if (elapsed >= period * 0.75) {
        sky.split = 0.5;
        sweeping = false;
        return;
      }
      const phase = (elapsed % period) / period;
      const tri = phase < 0.5 ? phase * 2 : 2 - phase * 2;
      sky.split = 0.02 + 0.96 * tri;
      rafId = requestAnimationFrame(loop);
    };
    rafId = requestAnimationFrame(loop);
  }

  function stopSweep(): void {
    sweeping = false;
    cancelAnimationFrame(rafId);
  }

  onDestroy(stopSweep);

  function dragTo(clientX: number): void {
    if (!wrapEl) return;
    const rect = wrapEl.getBoundingClientRect();
    sky.split = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
  }

  function onGripPointerDown(event: PointerEvent): void {
    stopSweep();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    dragTo(event.clientX);
  }

  function onGripPointerMove(event: PointerEvent): void {
    if ((event.currentTarget as HTMLElement).hasPointerCapture(event.pointerId)) {
      dragTo(event.clientX);
    }
  }
</script>

{#if sky.hasPair && sky.showFine}
  <div class="reveal-overlay" bind:this={wrapEl}>
    <span
      class="pill coarse"
      class:hidden={!leftPillVisible}
      style:left={`calc(${sky.split * 100}% - 12px)`}
    >{coarsePill}</span>
    <span
      class="pill fine"
      class:hidden={!rightPillVisible}
      style:left={`calc(${sky.split * 100}% + 12px)`}
    >{finePill}</span>

    <div class="divider" style:left={`calc(${sky.split * 100}% - 1px)`} aria-hidden="true"></div>
    <button
      class="grip"
      style:left={`${sky.split * 100}%`}
      onpointerdown={onGripPointerDown}
      onpointermove={onGripPointerMove}
      tabindex="-1"
      aria-hidden="true"
    >
      <svg viewBox="0 0 20 12" width="20" height="12">
        <path d="M7 1 L2 6 L7 11" fill="none" stroke="currentColor" stroke-width="1.6" />
        <path d="M13 1 L18 6 L13 11" fill="none" stroke="currentColor" stroke-width="1.6" />
      </svg>
    </button>

    <div class="reveal-controls">
      <label>
        <span class="vh">Reveal divider position</span>
        <input
          type="range"
          min="0"
          max="1"
          step="0.001"
          value={sky.split}
          oninput={(event) => {
            stopSweep();
            sky.split = Number(event.currentTarget.value);
          }}
          aria-valuetext={`${Math.round(sky.split * 100)}% coarse, ${Math.round((1 - sky.split) * 100)}% generated`}
        />
      </label>
      <button class="sweep" onclick={() => (sweeping ? stopSweep() : startSweep())}>
        {sweeping ? "Stop sweep" : "Sweep"}
      </button>
    </div>
  </div>
{/if}

<style>
  .reveal-overlay {
    position: absolute;
    inset: 0;
    z-index: 3;
    pointer-events: none;
  }

  /* Divider-pinned pills — they travel with the curtain so each side names
     itself at the point of comparison, not in a distant corner. */
  .pill {
    position: absolute;
    top: 24%;
    font-size: 11.5px;
    letter-spacing: 0.05em;
    white-space: nowrap;
    color: var(--text-dim);
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 5px;
    padding: 4px 10px;
    transition: opacity 0.25s ease;
  }

  .pill.coarse {
    transform: translateX(-100%);
  }

  .pill.fine {
    color: #fde68a;
    border-color: rgba(251, 191, 36, 0.45);
  }

  .pill.hidden {
    opacity: 0;
  }

  .divider {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 2px;
    background: rgba(203, 213, 225, 0.55);
    mix-blend-mode: screen;
  }

  .grip {
    position: absolute;
    top: 50%;
    transform: translate(-50%, -50%);
    pointer-events: auto;
    width: 34px;
    height: 26px;
    display: grid;
    place-items: center;
    color: var(--text);
    background: var(--panel);
    border: 1px solid rgba(203, 213, 225, 0.45);
    border-radius: 13px;
    cursor: ew-resize;
    touch-action: none;
  }

  .reveal-controls {
    position: absolute;
    left: 50%;
    bottom: 14px;
    transform: translateX(-50%);
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 6px;
    padding: 5px 10px;
  }

  .reveal-controls input[type="range"] {
    width: 160px;
    accent-color: var(--accent);
  }

  .sweep {
    font-size: 12px;
    color: var(--text);
    border: 1px solid var(--panel-border);
    border-radius: 4px;
    padding: 3px 10px;
  }

  .sweep:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .vh {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
  }
</style>
