/**
 * Imagery layer slots — Architecture.md §6.2, §6.3.
 *
 * Each rendered field is a LayerStack of three ImageryLayer slots:
 *   lower    frame i,   alpha pinned 1.0
 *   upper    frame i+1, alpha = clamp(frac, 0.02, 0.98) — the ONLY ramped layer
 *   preload  frame i+2, alpha 0.0 (skipped by the engine, but its texture is
 *            uploaded, so advancing a frame swaps textures without a fetch)
 *
 * The hero comparison is two stacks over the identical code path, LUT and
 * filter settings — coarse splitDirection LEFT, fine RIGHT. The wipe is a
 * per-fragment kill, never a blend (§6.3), so no colour is ever invented.
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
import { primaryLayerFor } from "../data/manifest";

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

  constructor(
    private scene: Scene,
    readonly def: LayerDef,
    private ring: BitmapRing,
    private split: SplitDirection,
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
    this.scene.imageryLayers.add(layer);
    return layer;
  }

  /** Filter parity probe for the coarse/fine assertion. */
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
  fine: LayerStack;
  coarse: LayerStack | null;
  hasPair: boolean;
  setFrame(i: number, frac: number): void;
  destroy(): void;
}

/**
 * Build the layer stacks for one variable: the hero coarse/fine pair when the
 * manifest declares one (wipe comparison), otherwise a single stack shown whole.
 */
export function buildVariableLayers(
  scene: Scene,
  manifest: Manifest,
  variable: Variable,
  ring: BitmapRing,
): VariableLayers {
  const fineDef = primaryLayerFor(manifest, variable);
  if (!fineDef) throw new Error(`manifest has no renderable layer for variable ${variable}`);

  let coarseDef: LayerDef | null = null;
  if (fineDef.pairWith !== undefined) {
    const candidate = manifest.layers.get(fineDef.pairWith);
    if (!candidate) throw new Error(`layer ${fineDef.id} pairWith ${fineDef.pairWith} does not exist`);
    if (candidate.kind === "hero-coarse") coarseDef = candidate;
  }

  // §7.2(b), re-asserted at load: the manifest identity strings for the pair
  // MUST be equal, or the reveal demonstrates a colour change, not a resolution
  // change. Refuse to render a false comparison.
  if (coarseDef && coarseDef.identity !== fineDef.identity) {
    throw new Error(
      `colour identity mismatch for ${variable}: coarse ${coarseDef.identity} != fine ${fineDef.identity} — ` +
        `the coarse and fine layers were not encoded through the same (LUT, vmin, vmax, alpha) tuple`,
    );
  }

  const fine = new LayerStack(scene, fineDef, ring, coarseDef ? SplitDirection.RIGHT : SplitDirection.NONE);
  const coarse = coarseDef ? new LayerStack(scene, coarseDef, ring, SplitDirection.LEFT) : null;

  // Warm the decode ring for every frame of the pair — decode off the frame path.
  ring.prefetch(fineDef.frameUrls);
  if (coarseDef) ring.prefetch(coarseDef.frameUrls);

  // §6.1 — identical minification/magnification filters on the hero pair. If
  // they travel through different filter settings, the reveal demonstrates a
  // pipeline change rather than a resolution change. Asserted once, on the
  // first frame both stacks have live layers.
  let parityAsserted = false;
  const assertFilterParity = (): void => {
    if (!coarse || parityAsserted) return;
    const a = fine.filters();
    const b = coarse.filters();
    if (a.min !== b.min || a.mag !== b.mag) {
      throw new Error(
        `filter parity violated on hero pair: fine(min=${a.min}, mag=${a.mag}) != coarse(min=${b.min}, mag=${b.mag})`,
      );
    }
    parityAsserted = true;
  };

  return {
    fine,
    coarse,
    hasPair: coarse !== null,
    setFrame(i: number, frac: number): void {
      fine.setFrame(i, frac);
      coarse?.setFrame(i, frac);
      assertFilterParity();
    },
    destroy(): void {
      fine.destroy();
      coarse?.destroy();
    },
  };
}
