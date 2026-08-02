/**
 * The globe façade — the ONLY surface the Svelte side may call.
 * Svelte never touches Cesium objects (§6.4); components call these setters
 * from $effect and nothing more.
 */
import type { CesiumWidget } from "cesium";
import type { Manifest, Variable } from "../data/manifest";
import { availableVariables, heroFineLayerFor } from "../data/manifest";
import { BitmapRing } from "./bitmapProvider";
import { CameraDirector, type HeroAnchor } from "./flight";
import { createRenderer } from "./render";
import { Timeline } from "./timeline";
import { createWidget, type WidgetOptions } from "./widget";

export type { HeroAnchor } from "./flight";

export interface GlobeApi {
  readonly frameCount: number;
  /** Whether the manifest ships a hero region to fly down to. */
  readonly heroAvailable: boolean;
  setFrame(f: number): void;
  setPlaying(playing: boolean): void;
  setSpeed(speed: number): void;
  setVariable(variable: Variable): void;
  setSplit(split: number): void;
  setShowFine(showFine: boolean): void;
  /** Engage/park the wipe — engaged only at the hero framing (§8.4). */
  setReveal(reveal: boolean): void;
  /** Arrival idle spin (§8.1). Stopped forever on first user interaction. */
  setIdleSpin(on: boolean): void;
  /** Fly down to the hero rectangle; done(completed) fires exactly once. */
  flyToHero(done: (completed: boolean) => void): void;
  /** Fly back out to the orbit framing. */
  flyToOrbit(done: (completed: boolean) => void): void;
  /** Hero-centre window coordinates after each rendered frame. */
  onAnchor(callback: (anchor: HeroAnchor | null) => void): () => void;
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

  const widget = createWidget(container, options, manifest.basemap);
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

  // The arrival (concept §8.1): the piece opens on the whole dark planet,
  // centred over the hero region so the invitation is on the near side. The
  // fly-down (§8.4) is the only way in to the hero framing.
  const heroFine =
    heroFineLayerFor(manifest, initialVariable) ??
    [...manifest.layers.values()].find((l) => l.kind === "hero-fine");
  const director = new CameraDirector(widget, heroFine ? heroFine.rect : null);
  director.setOrbitView();

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
    heroAvailable: director.heroAvailable,
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
    setReveal(reveal: boolean): void {
      if (reveal === renderer.state.reveal) return;
      renderer.applyState({ reveal });
    },
    setIdleSpin(on: boolean): void {
      if (on) director.startIdleSpin();
      else director.stopIdleSpin();
    },
    flyToHero(done: (completed: boolean) => void): void {
      director.flyToHero(done);
    },
    flyToOrbit(done: (completed: boolean) => void): void {
      director.flyToOrbit(done);
    },
    onAnchor(callback) {
      return director.onAnchor(callback);
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
      director.destroy();
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
