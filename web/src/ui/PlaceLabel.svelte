<script lang="ts">
  /**
   * The place label — from real demo feedback ("is that Vietnam?"): a quiet,
   * always-visible pill naming the hero domain. Same pill language as the
   * divider labels, dimmer, non-interactive.
   *
   * Placement: in orbit it anchors under the invitation (or docks with it,
   * low-centre, while the hero region is behind the limb); at the hero framing
   * it pins to the top edge of the hero rectangle via the anchor's projected
   * top-edge midpoint. It fades out during camera flights, matching the rest
   * of the chrome's motion language.
   */
  import { sky } from "../state/store.svelte";
  import type { HeroAnchor } from "../globe";

  let { anchor = null, visible = true }: { anchor?: HeroAnchor | null; visible?: boolean } =
    $props();

  // run.placeLabel is landing in the typed manifest loader separately; until
  // the typed field exists, read it defensively and fall back to the known
  // hero domain (the only public km-scale CorrDiff checkpoint is Taiwan).
  const label = $derived.by(() => {
    const run = sky.manifest?.run as { placeLabel?: unknown } | undefined;
    const value = run?.placeLabel;
    return typeof value === "string" && value.length > 0 ? value : "Taiwan · CWA model domain";
  });
</script>

{#if visible}
  {#if sky.view === "hero"}
    {#if anchor?.top}
      <!-- Pinned to the hero rectangle's top edge; clamped clear of the
           masthead above and the divider pills (top: 24%) below. -->
      <span
        class="place hero"
        class:flying={sky.flying}
        style:left={`clamp(120px, ${anchor.top.x}px, calc(100% - 120px))`}
        style:top={`clamp(58px, ${anchor.top.y - 10}px, 21%)`}
      >{label}</span>
    {:else}
      <span class="place hero fallback" class:flying={sky.flying}>{label}</span>
    {/if}
  {:else if anchor?.visible}
    <span
      class="place"
      class:flying={sky.flying}
      style:left={`${anchor.x}px`}
      style:top={`${anchor.y + 68}px`}
    >{label}</span>
  {:else}
    <span class="place docked" class:flying={sky.flying}>{label}</span>
  {/if}
{/if}

<style>
  /* The divider pills' language (RevealSlider), one step dimmer. */
  .place {
    position: absolute;
    z-index: 3; /* under the marker (4) and invitation (5) — never above them */
    transform: translateX(-50%);
    font-size: 11px;
    letter-spacing: 0.06em;
    white-space: nowrap;
    color: var(--text-faint);
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 5px;
    padding: 3px 9px;
    pointer-events: none;
    transition: opacity 0.25s ease;
  }

  .place.hero {
    /* Sit just above the hero rectangle's top edge. */
    transform: translate(-50%, -100%);
  }

  .place.hero.fallback {
    left: 50%;
    top: 58px;
    transform: translateX(-50%);
  }

  .place.docked {
    /* Above the docked invitation (bottom: 26px, ~34px tall). */
    left: 50%;
    bottom: 70px;
  }

  .place.flying {
    opacity: 0;
  }
</style>
