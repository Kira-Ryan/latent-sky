/**
 * Runes store — the single source of truth for UI state.
 * Components mutate this; App.svelte's $effects push changes into the globe
 * façade. Nothing in here imports Cesium (§6.4).
 */
import {
  availableVariables,
  globalLayerFor,
  primaryLayerFor,
  type LayerDef,
  type Manifest,
  type Variable,
} from "../data/manifest";

class SkyStore {
  manifest = $state<Manifest | null>(null);

  variable = $state<Variable>("wind10m");
  /** Fractional frame index in [0, frameCount - 1]. */
  frame = $state(0);
  playing = $state(false);
  speed = $state(1);
  /** Wipe divider position in [0, 1]. */
  split = $state(0.5);
  showFine = $state(true);

  /** Where the camera lives: in orbit (arrival state) or down at the hero. */
  view = $state<"orbit" | "hero">("orbit");
  /** True for the duration of a camera flight (either direction). */
  flying = $state(false);
  /** Set on arrival at the hero: the reveal plays ONE auto-sweep, then rests mid. */
  sweepPending = $state(false);
  /** The wipe is engaged only once the camera has settled at the hero (§8.4). */
  revealEngaged: boolean = $derived(this.view === "hero" && !this.flying);

  variables: Variable[] = $derived(this.manifest ? availableVariables(this.manifest) : []);
  frameCount: number = $derived(this.manifest?.frameIso.length ?? 0);
  activeLayer: LayerDef | undefined = $derived(
    this.manifest ? primaryLayerFor(this.manifest, this.variable) : undefined,
  );
  /**
   * The layer the legend should describe. From orbit the screen is dominated
   * by the global field, so its label (e.g. "ERA5 analysis, 0.5°", or t2m's
   * "clipped below −40 °C" note) is the honest one; the hero-fine label
   * applies only once the camera is down at the hero. Same LUT either way —
   * identity is enforced across layers — only the words change.
   */
  legendLayer: LayerDef | undefined = $derived.by(() => {
    if (!this.manifest) return undefined;
    if (this.view === "orbit") {
      return globalLayerFor(this.manifest, this.variable) ?? this.activeLayer;
    }
    return this.activeLayer;
  });
  /** Whether the active variable has a coarse/fine hero pair (the reveal). */
  hasPair: boolean = $derived(
    this.activeLayer?.pairWith !== undefined &&
      this.manifest?.layers.get(this.activeLayer.pairWith)?.kind === "hero-coarse",
  );
  /** aria-valuetext for the scrubber — always a valid time. */
  nearestFrameIso: string = $derived.by(() => {
    const m = this.manifest;
    if (!m) return "";
    const i = Math.min(Math.max(Math.round(this.frame), 0), m.frameIso.length - 1);
    return m.frameIso[i];
  });

  init(manifest: Manifest): void {
    this.manifest = manifest;
    const vars = availableVariables(manifest);
    if (vars.length === 0) throw new Error("manifest declares no renderable layers");
    this.variable = vars.includes("wind10m") ? "wind10m" : vars[0];
    this.frame = 0;
  }
}

export const sky = new SkyStore();
