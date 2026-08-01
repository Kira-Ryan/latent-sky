/**
 * Runes store — the single source of truth for UI state.
 * Components mutate this; App.svelte's $effects push changes into the globe
 * façade. Nothing in here imports Cesium (§6.4).
 */
import {
  availableVariables,
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

  variables: Variable[] = $derived(this.manifest ? availableVariables(this.manifest) : []);
  frameCount: number = $derived(this.manifest?.frameIso.length ?? 0);
  activeLayer: LayerDef | undefined = $derived(
    this.manifest ? primaryLayerFor(this.manifest, this.variable) : undefined,
  );
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
