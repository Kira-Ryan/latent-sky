/**
 * The single applyState() — Architecture.md §6.2, §10.
 *
 * requestRenderMode is on with maximumRenderTimeChange Infinity, so a forgotten
 * scene.requestRender() presents as a frozen UI rather than an error. EVERY
 * mutation of the globe therefore funnels through applyState(), which always
 * ends with scene.requestRender(). This is enforced by convention from commit
 * one: no other module calls into Cesium mutators.
 */
import type { CesiumWidget } from "cesium";
import type { Manifest, Variable } from "../data/manifest";
import type { BitmapRing } from "./bitmapProvider";
import { buildVariableLayers, type VariableLayers } from "./layers";
import type { Timeline } from "./timeline";

export interface GlobeState {
  /** Fractional frame index in [0, frameCount - 1]. */
  frame: number;
  variable: Variable;
  /** Wipe divider position in [0, 1] of the canvas width. */
  split: number;
  /** When false, the coarse field fills the view (divider pushed fully right). */
  showFine: boolean;
  /**
   * The wipe is engaged only at the hero framing (concept §8.4). When false
   * (orbit, or mid-flight) the divider parks fully off-canvas so the fine
   * field fills its rectangle — the storm reads whole from orbit, and the
   * comparison begins only once the camera has arrived.
   */
  reveal: boolean;
  playing: boolean;
  /** Playback speed multiplier relative to the timeline's base rate. */
  speed: number;
}

export interface ApplyOptions {
  /** The frame value originated from the ticking clock — do not write it back. */
  fromClock?: boolean;
}

export interface Renderer {
  readonly state: Readonly<GlobeState>;
  applyState(patch: Partial<GlobeState>, options?: ApplyOptions): void;
  stacks(): VariableLayers;
  destroy(): void;
}

export function createRenderer(
  widget: CesiumWidget,
  manifest: Manifest,
  timeline: Timeline,
  ring: BitmapRing,
  initialVariable: Variable,
): Renderer {
  const scene = widget.scene;
  const clock = widget.clock;

  const state: GlobeState = {
    frame: 0,
    variable: initialVariable,
    split: 0.5,
    showFine: true,
    reveal: false, // the piece opens in orbit — the wipe waits for arrival
    playing: false,
    speed: 1,
  };

  let layers: VariableLayers | null = null;
  let destroyed = false;

  function rebuildLayers(): void {
    layers?.destroy();
    layers = buildVariableLayers(scene, manifest, state.variable, ring);
    const { i, frac } = timeline.split(state.frame);
    layers.setFrame(i, frac);
    applySplit();
  }

  function applySplit(): void {
    // §6.3 — splitDirection is a per-fragment kill against czm_splitPosition.
    // showFine=false parks the divider hard right: coarse everywhere, zero
    // layer churn, zero shader permutation changes. reveal=false (orbit /
    // mid-flight) parks it hard left instead: the fine field fills its rect.
    if (!(layers?.hasPair && state.showFine)) {
      scene.splitPosition = 1.0;
    } else {
      scene.splitPosition = state.reveal ? state.split : 0.0;
    }
  }

  function applyState(patch: Partial<GlobeState>, options: ApplyOptions = {}): void {
    if (destroyed) throw new Error("applyState after destroy");

    if (patch.variable !== undefined && patch.variable !== state.variable) {
      state.variable = patch.variable;
      rebuildLayers();
    }
    if (layers === null) rebuildLayers();

    if (patch.frame !== undefined) {
      state.frame = Math.min(Math.max(patch.frame, 0), timeline.frameCount - 1);
      if (!options.fromClock) {
        clock.currentTime = timeline.timeForFrameFloat(state.frame);
      }
      const { i, frac } = timeline.split(state.frame);
      layers!.setFrame(i, frac);
    }

    if (patch.playing !== undefined) {
      state.playing = patch.playing;
      clock.shouldAnimate = patch.playing;
    }

    if (patch.speed !== undefined) {
      state.speed = patch.speed;
      clock.multiplier = timeline.baseMultiplier * patch.speed;
    }

    if (patch.split !== undefined || patch.showFine !== undefined || patch.reveal !== undefined) {
      if (patch.split !== undefined) state.split = Math.min(Math.max(patch.split, 0), 1);
      if (patch.showFine !== undefined) state.showFine = patch.showFine;
      if (patch.reveal !== undefined) state.reveal = patch.reveal;
      applySplit();
    }

    scene.requestRender(); // ALWAYS — §6.2
  }

  return {
    state,
    applyState,
    stacks(): VariableLayers {
      if (layers === null) throw new Error("layers not built yet — applyState first");
      return layers;
    },
    destroy(): void {
      destroyed = true;
      layers?.destroy();
      layers = null;
    },
  };
}
