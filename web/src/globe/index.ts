/**
 * The globe façade — the ONLY surface the Svelte side may call.
 * Svelte never touches Cesium objects (§6.4); components call these setters
 * from $effect and nothing more.
 */
import { Rectangle, type CesiumWidget } from "cesium";
import type { Manifest, Variable } from "../data/manifest";
import { availableVariables, primaryLayerFor } from "../data/manifest";
import { BitmapRing } from "./bitmapProvider";
import { createRenderer } from "./render";
import { Timeline } from "./timeline";
import { createWidget, type WidgetOptions } from "./widget";

export interface GlobeApi {
  readonly frameCount: number;
  setFrame(f: number): void;
  setPlaying(playing: boolean): void;
  setSpeed(speed: number): void;
  setVariable(variable: Variable): void;
  setSplit(split: number): void;
  setShowFine(showFine: boolean): void;
  /** Playback position feedback (fractional frame index) while the clock animates. */
  onFrame(callback: (f: number) => void): () => void;
  /** Test hook: force a render of the current state. */
  requestRender(): void;
  destroy(): void;
}

export async function createGlobe(
  container: HTMLElement,
  manifest: Manifest,
  options: WidgetOptions = {},
): Promise<GlobeApi> {
  const variables = availableVariables(manifest);
  if (variables.length === 0) throw new Error("manifest declares no renderable layers");
  const initialVariable: Variable = variables.includes("wind10m") ? "wind10m" : variables[0];

  const widget = createWidget(container, options);
  // The decode nudge and the load-progress hook below together keep the
  // requestRenderMode load cascade alive: Cesium re-requests renders only via
  // its own RequestScheduler, which the ImageBitmap ring bypasses by design.
  const ring = new BitmapRing(48, () => widget.scene.requestRender());
  const removeLoadNudge = widget.scene.globe.tileLoadProgressEvent.addEventListener(
    (remaining: number) => {
      if (remaining > 0) widget.scene.requestRender();
    },
  );
  const timeline = new Timeline(widget.clock, manifest.frameIso);
  const renderer = createRenderer(widget, manifest, timeline, ring, initialVariable);

  // Frame the hero rectangle with a little air around it.
  const heroDef = primaryLayerFor(manifest, initialVariable)!;
  const [west, south, east, north] = heroDef.rect;
  const padX = (east - west) * 0.6;
  const padY = (north - south) * 0.6;
  widget.camera.setView({
    destination: Rectangle.fromDegrees(west - padX, south - padY, east + padX, north + padY),
  });

  renderer.applyState({ frame: 0 }); // builds the initial stacks and requests the first render

  // Clock driver: while animating, clock time is the source of truth for frame.
  const frameCallbacks = new Set<(f: number) => void>();
  const removeTick = widget.clock.onTick.addEventListener((clock) => {
    if (!clock.shouldAnimate) return;
    const f = timeline.frameFloatFor(clock.currentTime);
    if (Math.abs(f - renderer.state.frame) < 1e-9) return;
    renderer.applyState({ frame: f }, { fromClock: true });
    for (const cb of frameCallbacks) cb(f);
  });

  // Resolve once every queued tile (base layer + field layers) has loaded, so
  // callers — and the smoke test — know the first real frame is on screen.
  await tilesLoaded(widget);

  return {
    frameCount: timeline.frameCount,
    setFrame(f: number): void {
      if (Math.abs(f - renderer.state.frame) < 1e-9) return; // breaks the store->clock->store echo
      renderer.applyState({ frame: f });
    },
    setPlaying(playing: boolean): void {
      if (playing === renderer.state.playing) return;
      renderer.applyState({ playing });
    },
    setSpeed(speed: number): void {
      if (speed === renderer.state.speed) return;
      renderer.applyState({ speed });
    },
    setVariable(variable: Variable): void {
      if (variable === renderer.state.variable) return;
      renderer.applyState({ variable });
    },
    setSplit(split: number): void {
      if (split === renderer.state.split) return;
      renderer.applyState({ split });
    },
    setShowFine(showFine: boolean): void {
      if (showFine === renderer.state.showFine) return;
      renderer.applyState({ showFine });
    },
    onFrame(callback: (f: number) => void): () => void {
      frameCallbacks.add(callback);
      return () => frameCallbacks.delete(callback);
    },
    requestRender(): void {
      widget.scene.requestRender();
    },
    destroy(): void {
      removeTick();
      removeLoadNudge();
      renderer.destroy();
      ring.destroy();
      widget.destroy();
    },
  };
}

function tilesLoaded(widget: CesiumWidget): Promise<void> {
  // globe.tilesLoaded is trivially true before the first render populates the
  // quadtree, so an unguarded check resolves before anything is on screen.
  // Require a loading phase to have been observed before trusting "loaded".
  const globe = widget.scene.globe;
  return new Promise((resolve) => {
    let seenLoading = false;
    const remove = globe.tileLoadProgressEvent.addEventListener((remaining: number) => {
      if (remaining > 0) {
        seenLoading = true;
        return;
      }
      if (seenLoading && globe.tilesLoaded) {
        remove();
        resolve();
      }
    });
    widget.scene.requestRender();
  });
}
