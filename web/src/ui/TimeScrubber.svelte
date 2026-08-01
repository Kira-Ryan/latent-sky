<script lang="ts">
  import { sky } from "../state/store.svelte";

  const max = $derived(Math.max(sky.frameCount - 1, 0));

  const timeFormat = new Intl.DateTimeFormat("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "UTC",
  });

  const timeText = $derived(
    sky.nearestFrameIso ? `${timeFormat.format(new Date(sky.nearestFrameIso))} UTC` : "",
  );

  function clampFrame(f: number): number {
    return Math.min(Math.max(f, 0), max);
  }

  function onKeydown(event: KeyboardEvent): void {
    let handled = true;
    switch (event.key) {
      case "ArrowLeft":
      case "ArrowDown":
        sky.playing = false;
        sky.frame = clampFrame(Math.round(sky.frame) - 1);
        break;
      case "ArrowRight":
      case "ArrowUp":
        sky.playing = false;
        sky.frame = clampFrame(Math.round(sky.frame) + 1);
        break;
      case "Home":
        sky.playing = false;
        sky.frame = 0;
        break;
      case "End":
        sky.playing = false;
        sky.frame = max;
        break;
      case " ":
        sky.playing = !sky.playing;
        break;
      default:
        handled = false;
    }
    if (handled) event.preventDefault();
  }
</script>

<div class="scrubber">
  <button
    class="play"
    onclick={() => (sky.playing = !sky.playing)}
    aria-label={sky.playing ? "Pause" : "Play"}
  >
    {#if sky.playing}
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <rect x="3" y="2.5" width="3.4" height="11" rx="1" fill="currentColor" />
        <rect x="9.6" y="2.5" width="3.4" height="11" rx="1" fill="currentColor" />
      </svg>
    {:else}
      <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
        <path d="M4 2.5 L13.5 8 L4 13.5 Z" fill="currentColor" />
      </svg>
    {/if}
  </button>

  <input
    class="range"
    type="range"
    min="0"
    {max}
    step="any"
    value={sky.frame}
    disabled={max === 0}
    oninput={(event) => {
      sky.frame = Number(event.currentTarget.value);
    }}
    onkeydown={onKeydown}
    aria-label="Valid time"
    aria-valuemin={0}
    aria-valuemax={max}
    aria-valuenow={sky.frame}
    aria-valuetext={timeText}
  />

  <span class="time">{timeText}</span>

  <label class="speed">
    <span class="vh">Playback speed</span>
    <select bind:value={sky.speed}>
      <option value={0.5}>0.5×</option>
      <option value={1}>1×</option>
      <option value={2}>2×</option>
    </select>
  </label>
</div>

<style>
  .scrubber {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
    min-width: 280px;
  }

  .play {
    flex: none;
    width: 30px;
    height: 30px;
    display: grid;
    place-items: center;
    border: 1px solid var(--panel-border);
    border-radius: 50%;
    color: var(--text);
  }

  .play:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .range {
    flex: 1;
    accent-color: var(--accent);
    min-width: 120px;
  }

  .time {
    flex: none;
    font-variant-numeric: tabular-nums;
    font-size: 12px;
    color: var(--text-dim);
    min-width: 15ch;
  }

  .speed select {
    font: inherit;
    font-size: 12px;
    color: var(--text);
    background: transparent;
    border: 1px solid var(--panel-border);
    border-radius: 4px;
    padding: 2px 4px;
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
