/**
 * The globe façade — the ONLY surface the Svelte side may call.
 * Svelte never touches Cesium objects (§6.4); components call these setters
 * from $effect and nothing more.
 *
 * ——— Widget lifetime vs SESSION lifetime ———
 *
 * The CesiumWidget is created once and lives for the page. Everything that is
 * a function of a particular manifest — base imagery layer, Timeline, Renderer
 * (and therefore every LayerStack), CameraDirector, clock tick listener — is a
 * SESSION, and `load()` replaces the session wholesale. That split is what
 * makes the event switcher safe:
 *
 *   · a widget per event would leak WebGL contexts. CesiumWidget.destroy()
 *     tears down the scene but never calls WEBGL_lose_context (verified in the
 *     1.143.0 build), so contexts accumulate until the browser force-loses the
 *     oldest — which, after enough switches, is the one on screen.
 *   · a session per event means the disposal list is explicit and auditable,
 *     and it ends with `imageryLayers.removeAll(true)` plus an assertion that
 *     the collection is empty. There is no path by which a previous event's
 *     layer can survive into the next one.
 *
 * The BitmapRing is deliberately widget-scoped, not session-scoped: it is
 * cleared (every ImageBitmap closed) between sessions, at the one moment when
 * the outgoing layers are gone and the incoming ones do not exist yet, so no
 * live layer can ever be awaiting a bitmap that is being closed.
 */
import type { CesiumWidget } from "cesium";
import type { Manifest, Variable } from "../data/manifest";
import { availableVariables, heroFineLayerFor } from "../data/manifest";
import { BitmapRing } from "./bitmapProvider";
import { CameraDirector, type HeroAnchor } from "./flight";
import { createRenderer, type Renderer } from "./render";
import { Timeline } from "./timeline";
import { createBaseLayer, createWidget, type WidgetOptions } from "./widget";

export type { HeroAnchor } from "./flight";

export interface GlobeApi {
  /** Frames in the CURRENTLY LOADED manifest; 0 before the first load(). */
  readonly frameCount: number;
  /** Whether the loaded manifest ships a hero region to fly down to. */
  readonly heroAvailable: boolean;
  /** True once a session is live — every setter below is inert until then. */
  readonly loaded: boolean;
  /**
   * Adopt a manifest: dispose the previous session completely, build a fresh
   * one, and resolve once its first frame is on screen. This is the event
   * switch.
   */
  load(manifest: Manifest): Promise<void>;
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
  /** Hero-centre window coordinates after each rendered frame. Survives switches. */
  onAnchor(callback: (anchor: HeroAnchor | null) => void): () => void;
  /** Playback position feedback (fractional frame index). Survives switches. */
  onFrame(callback: (f: number) => void): () => void;
  /** Test hook: force a render of the current state. */
  requestRender(): void;
  destroy(): void;
}

interface Session {
  timeline: Timeline;
  renderer: Renderer;
  director: CameraDirector;
  removeTick: () => void;
  removeAnchor: () => void;
}

export async function createGlobe(
  container: HTMLElement,
  options: WidgetOptions = {},
): Promise<GlobeApi> {
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

  // Subscriber sets live at the façade, not in the session, so a component that
  // subscribed once at mount keeps receiving events across every switch.
  const frameCallbacks = new Set<(f: number) => void>();
  const anchorCallbacks = new Set<(anchor: HeroAnchor | null) => void>();

  let session: Session | null = null;
  let destroyed = false;

  /**
   * Dispose the live session. Synchronous and total — there is no await inside,
   * so no $effect can interleave between "old session gone" and "new session
   * built", and therefore no setter can ever reach a half-torn-down globe.
   */
  function disposeSession(): void {
    const dying = session;
    if (!dying) return;
    session = null;

    dying.removeTick(); // stop driving frames from the old timeline's clock
    dying.removeAnchor(); // stop projecting the old hero rectangle
    dying.director.destroy(); // rAF loops, in-flight camera tween, zoom floor
    dying.renderer.destroy(); // every LayerStack slot removed from the scene

    // The audit. renderer.destroy() removes each field layer it owns; this
    // removes those plus the session's base layer at index 0, so the collection
    // is provably empty rather than expected to be. The assertion turns any
    // future layer added outside a stack into a loud failure at the moment it
    // would otherwise have become a stale layer on the next event.
    widget.scene.imageryLayers.removeAll(true);
    if (widget.scene.imageryLayers.length !== 0) {
      throw new Error(
        `event switch leaked ${widget.scene.imageryLayers.length} imagery layer(s) — ` +
          "every layer must be owned by the session's renderer or base layer",
      );
    }

    // Safe exactly here: the outgoing layers are gone and the incoming ones do
    // not exist, so no live ImageryLayer is awaiting any of these bitmaps.
    ring.clear();

    widget.clock.shouldAnimate = false;
    widget.scene.splitPosition = 0.0;
    widget.scene.requestRender();
  }

  async function load(manifest: Manifest): Promise<void> {
    if (destroyed) throw new Error("load() after destroy");

    const variables = availableVariables(manifest);
    if (variables.length === 0) throw new Error("manifest declares no renderable layers");
    const initialVariable: Variable = variables.includes("wind10m") ? "wind10m" : variables[0];

    disposeSession();

    // Everything below can throw on a manifest the schema admits but the
    // renderer refuses — a colour-identity mismatch between a coarse/fine pair
    // is a deliberate build failure (§7.2b) that reaches the app as an
    // exception. Build under a rollback so a rejected manifest leaves NOTHING
    // attached to the shared widget: no half-built session is still a session.
    let renderer: Renderer | undefined;
    let director: CameraDirector | undefined;
    let removeAnchor: (() => void) | undefined;
    try {
      // Base imagery first, so it lands at collection index 0 and every field
      // stack composites above it.
      const baseLayer = createBaseLayer(manifest.basemap);
      widget.scene.imageryLayers.add(baseLayer, 0);

      // Timeline's constructor rewrites clock start/stop/current/range/multiplier
      // from the new frames — the whole reason a switch cannot inherit a clock.
      const timeline = new Timeline(widget.clock, manifest.frameIso);
      renderer = createRenderer(widget, manifest, timeline, ring, initialVariable);

      // The arrival (concept §8.1): the piece opens on the whole dark planet,
      // centred over the hero region so the invitation is on the near side. The
      // fly-down (§8.4) is the only way in to the hero framing.
      const heroFine =
        heroFineLayerFor(manifest, initialVariable) ??
        [...manifest.layers.values()].find((l) => l.kind === "hero-fine");
      director = new CameraDirector(widget, heroFine ? heroFine.rect : null);
      director.setOrbitView();
      const anchorDirector = director;
      removeAnchor = anchorDirector.onAnchor((a) => {
        for (const cb of anchorCallbacks) cb(a);
      });

      renderer.applyState({ frame: 0 }); // builds the initial stacks and requests the first render

      // Clock driver: while animating, clock time is the source of truth for frame.
      const sessionRenderer = renderer;
      const removeTick = widget.clock.onTick.addEventListener((clock) => {
        if (!clock.shouldAnimate) return;
        const f = timeline.frameFloatFor(clock.currentTime);
        if (Math.abs(f - sessionRenderer.state.frame) < 1e-9) return;
        sessionRenderer.applyState({ frame: f }, { fromClock: true });
        for (const cb of frameCallbacks) cb(f);
      });

      session = { timeline, renderer, director, removeTick, removeAnchor };
    } catch (err: unknown) {
      removeAnchor?.();
      director?.destroy();
      renderer?.destroy();
      widget.scene.imageryLayers.removeAll(true);
      ring.clear();
      throw err; // the manifest is rejected loudly; the globe is left clean, not half-built
    }

    // Resolve once every queued tile (base layer + field layers) has loaded, so
    // callers — and the smoke test — know the first real frame is on screen.
    await tilesLoaded(widget);
  }

  return {
    get frameCount(): number {
      return session?.timeline.frameCount ?? 0;
    },
    get heroAvailable(): boolean {
      return session?.director.heroAvailable ?? false;
    },
    get loaded(): boolean {
      return session !== null;
    },
    load,
    // Every setter is inert without a session. That is a real state, not a
    // swallowed error: it is the window between createGlobe() and the first
    // load(), and the window during a switch, and a $effect firing then has
    // nothing legitimate to say to a globe that holds no data.
    setFrame(f: number): void {
      if (!session || Math.abs(f - session.renderer.state.frame) < 1e-9) return; // breaks the store->clock->store echo
      session.renderer.applyState({ frame: f });
    },
    setPlaying(playing: boolean): void {
      if (!session || playing === session.renderer.state.playing) return;
      session.renderer.applyState({ playing });
    },
    setSpeed(speed: number): void {
      if (!session || speed === session.renderer.state.speed) return;
      session.renderer.applyState({ speed });
    },
    setVariable(variable: Variable): void {
      if (!session || variable === session.renderer.state.variable) return;
      session.renderer.applyState({ variable });
    },
    setSplit(split: number): void {
      if (!session || split === session.renderer.state.split) return;
      session.renderer.applyState({ split });
    },
    setShowFine(showFine: boolean): void {
      if (!session || showFine === session.renderer.state.showFine) return;
      session.renderer.applyState({ showFine });
    },
    setReveal(reveal: boolean): void {
      if (!session || reveal === session.renderer.state.reveal) return;
      session.renderer.applyState({ reveal });
    },
    setIdleSpin(on: boolean): void {
      if (!session) return;
      if (on) session.director.startIdleSpin();
      else session.director.stopIdleSpin();
    },
    flyToHero(done: (completed: boolean) => void): void {
      if (!session) {
        done(false);
        return;
      }
      session.director.flyToHero(done);
    },
    flyToOrbit(done: (completed: boolean) => void): void {
      if (!session) {
        done(false);
        return;
      }
      session.director.flyToOrbit(done);
    },
    onAnchor(callback): () => void {
      anchorCallbacks.add(callback);
      return () => anchorCallbacks.delete(callback);
    },
    onFrame(callback: (f: number) => void): () => void {
      frameCallbacks.add(callback);
      return () => frameCallbacks.delete(callback);
    },
    requestRender(): void {
      widget.scene.requestRender();
    },
    destroy(): void {
      destroyed = true;
      disposeSession();
      frameCallbacks.clear();
      anchorCallbacks.clear();
      removeLoadNudge();
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
