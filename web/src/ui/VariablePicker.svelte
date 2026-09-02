<script lang="ts">
  import { sky } from "../state/store.svelte";
  import type { Variable } from "../data/manifest";

  const NAMES: Record<Variable, string> = {
    wind10m: "Wind",
    t2m: "Temperature",
    mrr: "Reflectivity",
    // Same word as mrr on purpose: to a viewer both are radar reflectivity. They
    // are separate variables only because their value ranges differ (mrr 0-55 dBZ
    // from CWB samples, refc 0-75 dBZ for US supercells), and §7.2b fixes a
    // variable's range globally and forever.
    refc: "Reflectivity",
    tcwv: "Water vapour",
    msl: "Pressure",
  };
</script>

<div class="picker">
  <div role="radiogroup" aria-label="Variable" class="group">
    {#each sky.variables as v (v)}
      <button
        role="radio"
        aria-checked={sky.variable === v}
        class:active={sky.variable === v}
        onclick={() => (sky.variable = v)}
      >
        {NAMES[v]}
      </button>
    {/each}
  </div>
  {#if sky.hasPair && sky.revealEngaged}
    <!-- Only meaningful at the hero framing — in orbit the toggle would
         silently restyle a few dozen pixels of a distant rectangle. -->
    <label class="fine-toggle">
      <input type="checkbox" bind:checked={sky.showFine} />
      Generated detail
    </label>
  {/if}
</div>

<style>
  .picker {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 8px;
  }

  .group {
    display: flex;
    flex-wrap: wrap; /* below ~560px the pills wrap rather than clip */
    justify-content: flex-end;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 6px;
    overflow: hidden;
  }

  .group button {
    padding: 6px 12px;
    font-size: 12px;
    color: var(--text-dim);
    border-right: 1px solid var(--panel-border);
  }

  .group button:last-child {
    border-right: none;
  }

  .group button:hover {
    color: var(--text);
  }

  .group button.active {
    color: var(--accent);
    background: rgba(79, 209, 197, 0.08);
  }

  .fine-toggle {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-dim);
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 6px;
    padding: 4px 10px;
    cursor: pointer;
  }

  .fine-toggle input {
    /* Amber: this toggle governs the generated layer — the hero accent. */
    accent-color: var(--amber);
  }
</style>
