/**
 * Imagery layer slots — Architecture.md §6.2, §6.3.
 *
 * Each rendered field is a LayerStack of three ImageryLayer slots:
 *   lower    frame i,   alpha pinned 1.0
 *   upper    frame i+1, alpha = clamp(frac, 0.02, 0.98) — the ONLY ramped layer
 *   preload  frame i+2, alpha 0.0 (skipped by the engine, but its texture is
 *            uploaded, so advancing a frame swaps textures without a fetch)
 *
 * A variable renders as up to three stacks, composited bottom to top:
 *   basemap (collection index 0, owned by widget.ts)
 *   < global       full-globe field, SplitDirection.NONE — NEVER splits
 *   < hero-coarse  splitDirection LEFT   ┐ the wipe applies ONLY to this pair;
 *   < hero-fine    splitDirection RIGHT  ┘ a per-fragment kill, never a blend (§6.3)
 *
 * All stacks travel the identical code path, LUT and filter settings — if they
 * diverge, the reveal demonstrates a pipeline change rather than a resolution
 * change. Every slot insertion is band-ordered: a stack's new layers are placed
 * below the lowest live layer of any stack above it, so cross-fade rotation can
 * never hoist a global slot above the hero pair.
 */
import {
  ImageryLayer,
  SplitDirection,
  TextureMagnificationFilter,
  TextureMinificationFilter,
  type Scene,
} from "cesium";
import { BitmapImageryProvider, type BitmapRing } from "./bitmapProvider";
import type { LayerDef, Manifest, Variable } from "../data/manifest";
import { globalLayerFor, heroFineLayerFor } from "../data/manifest";

export const ALPHA_EPS = 0.02; // §6.2 — crossing 0.0 or 1.0 mid-animation recompiles shaders

// One filter pair for every field layer. Magnification NEAREST keeps model
// cells crisp at hero zoom (the blocky coarse field is honest — §13);
// minification LINEAR avoids shimmer at globe framing. Images are
// non-power-of-two by construction, so generateMipmap is never called.
const MIN_FILTER = TextureMinificationFilter.LINEAR;
const MAG_FILTER = TextureMagnificationFilter.NEAREST;

class LayerStack {
  private slots: { layer: ImageryLayer; frame: number }[] = [];
  private destroyed = false;

  /**
   * `above` returns the collection index of the lowest live layer belonging to
   * any stack composited ABOVE this one, or undefined when this stack is
   * topmost. New slots are inserted at that index, keeping bands ordered.
   */
  constructor(
    private scene: Scene,
    readonly def: LayerDef,
    private ring: BitmapRing,
    private split: SplitDirection,
    private above: () => number | undefined = () => undefined,
  ) {}

  private wrap(k: number): number {
    const n = this.def.frameUrls.length;
    return ((k % n) + n) % n;
  }

  private makeLayer(frame: number, alpha: number): ImageryLayer {
    const provider = new BitmapImageryProvider({
      rect: this.def.rect,
      size: this.def.size,
      source: () => this.ring.get(this.def.frameUrls[frame]),
    });
    const layer = new ImageryLayer(provider.asProvider(), {
      alpha,
      splitDirection: this.split,
      minificationFilter: MIN_FILTER,
      magnificationFilter: MAG_FILTER,
      // Never touch brightness / contrast / hue / saturation / gamma (§7.2c).
    });
    const ceiling = this.above();
    if (ceiling === undefined) this.scene.imageryLayers.add(layer);
    else this.scene.imageryLayers.add(layer, ceiling);
    return layer;
  }

  /** Collection index of this stack's lowest live layer, or undefined if none. */
  lowestIndex(): number | undefined {
    let min: number | undefined;
    for (const slot of this.slots) {
      const i = this.scene.imageryLayers.indexOf(slot.layer);
      if (i >= 0 && (min === undefined || i < min)) min = i;
    }
    return min;
  }

  /** Filter parity probe for the cross-stack assertion. */
  filters(): { min: TextureMinificationFilter; mag: TextureMagnificationFilter } {
    const layer = this.slots[0]?.layer;
    if (!layer) throw new Error(`LayerStack ${this.def.id} has no slots yet`);
    return { min: layer.minificationFilter, mag: layer.magnificationFilter };
  }

  private upperAlpha(frac: number): number {
    return Math.min(1 - ALPHA_EPS, Math.max(ALPHA_EPS, frac));
  }

  /**
   * Position the stack at frame index i with cross-fade fraction frac in [0, 1).
   * Lower layer alpha is pinned at 1.0; ONLY the upper layer ramps (§6.2).
   */
  setFrame(i: number, frac: number): void {
    if (this.destroyed) throw new Error(`setFrame on destroyed stack ${this.def.id}`);
    const n = this.def.frameUrls.length;

    if (n === 1) {
      if (this.slots.length === 0) this.slots.push({ layer: this.makeLayer(0, 1.0), frame: 0 });
      return;
    }

    const lower = this.wrap(i);
    const upper = this.wrap(i + 1);
    const preload = this.wrap(i + 2);

    if (this.slots.length === 3 && this.slots[0].frame === lower) {
      // Same base frame — ramp the upper layer only.
      this.slots[1].layer.alpha = this.upperAlpha(frac);
      return;
    }

    if (this.slots.length === 3 && this.slots[1].frame === lower) {
      // Advanced by exactly one frame: rotate. The old preload's texture is
      // already uploaded, so this is alpha bookkeeping, not I/O.
      const [old, newLower, newUpper] = this.slots;
      this.scene.imageryLayers.remove(old.layer, true);
      newLower.layer.alpha = 1.0;
      newUpper.layer.alpha = this.upperAlpha(frac);
      this.slots = [newLower, newUpper, { layer: this.makeLayer(preload, 0.0), frame: preload }];
      return;
    }

    // Jump (scrub, loop restart, first call): rebuild all three slots.
    for (const slot of this.slots) this.scene.imageryLayers.remove(slot.layer, true);
    this.slots = [
      { layer: this.makeLayer(lower, 1.0), frame: lower },
      { layer: this.makeLayer(upper, this.upperAlpha(frac)), frame: upper },
      { layer: this.makeLayer(preload, 0.0), frame: preload },
    ];
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    for (const slot of this.slots) this.scene.imageryLayers.remove(slot.layer, true);
    this.slots = [];
  }
}

export interface VariableLayers {
  /** Full-globe stack, below the hero layers; never splits. Null if the variable has none. */
  global: LayerStack | null;
  /** Hero fine stack. Null for global-only variables (e.g. tcwv). */
  fine: LayerStack | null;
  coarse: LayerStack | null;
  hasPair: boolean;
  setFrame(i: number, frac: number): void;
  destroy(): void;
}

/**
 * Build the layer stacks for one variable: its global stack (if any) AND its
 * hero layers (if any), shown together. The wipe applies only to the hero pair.
 */
export function buildVariableLayers(
  scene: Scene,
  manifest: Manifest,
  variable: Variable,
  ring: BitmapRing,
): VariableLayers {
  const globalDef = globalLayerFor(manifest, variable) ?? null;
  const fineDef = heroFineLayerFor(manifest, variable) ?? null;
  if (!globalDef && !fineDef) throw new Error(`manifest has no renderable layer for variable ${variable}`);

  let coarseDef: LayerDef | null = null;
  if (fineDef?.pairWith !== undefined) {
    const candidate = manifest.layers.get(fineDef.pairWith);
    if (!candidate) throw new Error(`layer ${fineDef.id} pairWith ${fineDef.pairWith} does not exist`);
    // The left side of the wipe: a coarse model input, or an observed field
    // (MRMS radar) the forecast is compared against. Same slot, same split.
    if (candidate.kind === "hero-coarse" || candidate.kind === "hero-observed") coarseDef = candidate;
  }

  // §7.2(b), re-asserted at load: every layer of this variable MUST share one
  // identity string, or the piece demonstrates a colour change, not a
  // resolution change. Refuse to render a false comparison.
  if (coarseDef && fineDef && coarseDef.identity !== fineDef.identity) {
    throw new Error(
      `colour identity mismatch for ${variable}: coarse ${coarseDef.identity} != fine ${fineDef.identity} — ` +
        `the coarse and fine layers were not encoded through the same (LUT, vmin, vmax, alpha) tuple`,
    );
  }
  const heroDef = fineDef ?? coarseDef;
  if (globalDef && heroDef && globalDef.identity !== heroDef.identity) {
    throw new Error(
      `colour identity mismatch for ${variable}: global ${globalDef.identity} != hero ${heroDef.identity} — ` +
        `the global and hero layers were not encoded through the same (LUT, vmin, vmax, alpha) tuple`,
    );
  }

  // Compositing order, bottom to top: global < coarse < fine. Each stack
  // inserts its slots below the lowest live layer of the stacks above it.
  // Declared before construction because the `above` closures reference the
  // stacks built after them; the closures only run at slot-insertion time.
  let global: LayerStack | null = null;
  let coarse: LayerStack | null = null;
  let fine: LayerStack | null = null;
  const lowestOf = (...stacks: (LayerStack | null)[]): number | undefined => {
    let min: number | undefined;
    for (const stack of stacks) {
      const i = stack?.lowestIndex();
      if (i !== undefined && (min === undefined || i < min)) min = i;
    }
    return min;
  };
  if (globalDef) {
    // The global layer never splits — it renders whole on both sides of the wipe.
    global = new LayerStack(scene, globalDef, ring, SplitDirection.NONE, () => lowestOf(coarse, fine));
  }
  if (coarseDef) {
    coarse = new LayerStack(scene, coarseDef, ring, SplitDirection.LEFT, () => lowestOf(fine));
  }
  if (fineDef) {
    fine = new LayerStack(scene, fineDef, ring, coarseDef ? SplitDirection.RIGHT : SplitDirection.NONE);
  }

  // Warm the decode ring for every frame of every stack — decode off the frame path.
  for (const def of [globalDef, coarseDef, fineDef]) {
    if (def) ring.prefetch(def.frameUrls);
  }

  // §6.1 — identical minification/magnification filters on every stack of the
  // variable. If they travel through different filter settings, the comparison
  // demonstrates a pipeline change rather than a resolution change. Asserted
  // once, on the first frame all stacks have live layers.
  const stacks = [global, coarse, fine].filter((s): s is LayerStack => s !== null);
  let parityAsserted = false;
  const assertFilterParity = (): void => {
    if (stacks.length < 2 || parityAsserted) return;
    const [first, ...rest] = stacks;
    const a = first.filters();
    for (const other of rest) {
      const b = other.filters();
      if (a.min !== b.min || a.mag !== b.mag) {
        throw new Error(
          `filter parity violated for ${variable}: ${first.def.id}(min=${a.min}, mag=${a.mag}) != ` +
            `${other.def.id}(min=${b.min}, mag=${b.mag})`,
        );
      }
    }
    parityAsserted = true;
  };

  return {
    global,
    fine,
    coarse,
    hasPair: coarse !== null && fine !== null,
    setFrame(i: number, frac: number): void {
      // Bottom-up, so first-ever slot creation lands in band order too.
      global?.setFrame(i, frac);
      coarse?.setFrame(i, frac);
      fine?.setFrame(i, frac);
      assertFilterParity();
    },
    destroy(): void {
      global?.destroy();
      coarse?.destroy();
      fine?.destroy();
    },
  };
}
