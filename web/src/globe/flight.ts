/**
 * Camera direction — the arrival (concept §8.1) and the fly-down (§8.4).
 *
 * Framework-free, like everything under globe/. Three responsibilities:
 *
 *   1. The idle spin: the page opens on the dark planet already slowly
 *      rotating. requestRenderMode only re-renders on request, so the spin
 *      loop must request a render every frame — same rule as the decode
 *      nudge in index.ts. The spin stops on first user interaction and never
 *      restarts (§8.1: motion is the loading state, not a screensaver).
 *
 *   2. The flights: orbit -> hero rectangle and back. Cesium advances camera
 *      tweens inside Scene.render, so with requestRenderMode a flight that
 *      nobody keeps requesting renders for simply freezes mid-air — the same
 *      starvation class as §6.5. A keep-alive rAF loop runs for exactly the
 *      duration of each flight.
 *
 *   3. The hero anchor: window coordinates of the hero region's centre, so
 *      the UI can pin the invitation over the storm while it is on the near
 *      side of the planet (concept §8.4 — "a labelled marker on the globe").
 *
 * Every path respects motionOk(): under prefers-reduced-motion there is no
 * spin and every flight is a jump cut (§8.4, build-phase gate).
 */
import {
  BoundingSphere,
  Cartesian2,
  Cartesian3,
  EasingFunction,
  Occluder,
  Rectangle,
  SceneTransforms,
  type CesiumWidget,
} from "cesium";
import { motionOk } from "../motion";

/** Fraction of the hero rect's span added on each side — comfortable air. */
const HERO_PAD = 0.35;
/** Orbit altitude in metres: the whole planet with quiet room around it
 *  (13.5e6 clipped the limb at 1280×800 — measured from the capture). */
const ORBIT_HEIGHT = 17_500_000;
/** Idle spin rate, radians per second (≈ 0.4°/s — perceptible, calm). */
const SPIN_RATE = 0.4 * (Math.PI / 180);
/** Fly-down duration, seconds (§8.4 asks for ~4–5 s with gentle easing). */
const FLY_SECONDS = 4.5;

export interface HeroAnchor {
  /** Window (CSS pixel) coordinates of the hero centre. */
  x: number;
  y: number;
  /** False while the hero region is behind the planet's limb. */
  visible: boolean;
  /**
   * Window coordinates of the hero rectangle's top-edge midpoint — the place
   * label's pin at the hero framing — or null while it is unprojectable
   * (behind the limb, or off-screen).
   */
  top: { x: number; y: number } | null;
}

export class CameraDirector {
  private readonly heroRect: Rectangle | null;
  private readonly heroCentre: Cartesian3 | null;
  /** Midpoint of the hero rectangle's top (northern) edge — the place label's pin. */
  private readonly heroTopCentre: Cartesian3 | null;
  private readonly orbitDestination: Cartesian3;
  // Occludee test against a sphere just inside the ellipsoid's minor radius,
  // so a point sitting exactly on the surface never flickers at the limb.
  private readonly occluder = new Occluder(
    new BoundingSphere(Cartesian3.ZERO, 6_350_000),
    new Cartesian3(1, 0, 0),
  );
  private readonly anchorScratch = new Cartesian2();
  private readonly topScratch = new Cartesian2();
  private spinRaf = 0;
  private keepAliveRaf = 0;
  private destroyed = false;

  /**
   * @param heroRectDegrees [west, south, east, north] of the hero-fine layer,
   *   or null when the manifest ships no hero region (global-only build).
   */
  constructor(
    private readonly widget: CesiumWidget,
    heroRectDegrees: [number, number, number, number] | null,
  ) {
    if (heroRectDegrees) {
      const [west, south, east, north] = heroRectDegrees;
      const padX = (east - west) * HERO_PAD;
      const padY = (north - south) * HERO_PAD;
      this.heroRect = Rectangle.fromDegrees(
        Math.max(west - padX, -180),
        Math.max(south - padY, -90),
        Math.min(east + padX, 180),
        Math.min(north + padY, 90),
      );
      this.heroCentre = Cartesian3.fromDegrees((west + east) / 2, (south + north) / 2);
      this.heroTopCentre = Cartesian3.fromDegrees((west + east) / 2, north);
      this.orbitDestination = Cartesian3.fromDegrees(
        (west + east) / 2,
        (south + north) / 2,
        ORBIT_HEIGHT,
      );
    } else {
      this.heroRect = null;
      this.heroCentre = null;
      this.heroTopCentre = null;
      this.orbitDestination = Cartesian3.fromDegrees(0, 15, ORBIT_HEIGHT);
    }
  }

  get heroAvailable(): boolean {
    return this.heroRect !== null;
  }

  /** Place the camera at the orbit framing instantly (the arrival state). */
  setOrbitView(): void {
    this.widget.camera.setView({ destination: this.orbitDestination });
    this.widget.scene.requestRender();
  }

  /**
   * Start the idle spin. No-op under prefers-reduced-motion (a static planet
   * IS the reduced-motion arrival). Returns immediately if already spinning.
   */
  startIdleSpin(): void {
    if (this.destroyed || this.spinRaf !== 0 || !motionOk()) return;
    let last = performance.now();
    const tick = (now: number): void => {
      if (this.spinRaf === 0) return;
      const dt = Math.min((now - last) / 1000, 0.1); // clamp tab-restore jumps
      last = now;
      // Rotate the camera about the planet's axis: the globe appears to turn
      // while distance and framing hold. requestRender every frame — §6.5.
      this.widget.camera.rotate(Cartesian3.UNIT_Z, -SPIN_RATE * dt);
      this.widget.scene.requestRender();
      this.spinRaf = requestAnimationFrame(tick);
    };
    this.spinRaf = requestAnimationFrame(tick);
  }

  stopIdleSpin(): void {
    if (this.spinRaf !== 0) {
      cancelAnimationFrame(this.spinRaf);
      this.spinRaf = 0;
    }
  }

  /**
   * Fly down to the hero rectangle (§8.4): ~4.5 s, gentle easing. Under
   * prefers-reduced-motion this is a jump cut. `done(completed)` always fires
   * exactly once; completed=false means the viewer cancelled by grabbing the
   * globe mid-flight, in which case the arrival sweep should not run.
   */
  flyToHero(done: (completed: boolean) => void): void {
    if (!this.heroRect) throw new Error("flyToHero: manifest declares no hero region");
    this.stopIdleSpin();
    this.fly(this.heroRect, done);
  }

  /** Fly back out to the orbit framing (§8.4 — "Return to orbit"). */
  flyToOrbit(done: (completed: boolean) => void): void {
    this.stopIdleSpin();
    this.fly(this.orbitDestination, done);
  }

  private fly(destination: Rectangle | Cartesian3, done: (completed: boolean) => void): void {
    const { camera, scene } = this.widget;
    if (!motionOk()) {
      camera.setView({ destination });
      scene.requestRender();
      done(true);
      return;
    }
    this.stopKeepAlive();
    // Keep-alive: tweens advance only inside renders, and requestRenderMode
    // issues none unaided — without this loop the flight freezes (§6.5 class).
    const keep = (): void => {
      if (this.keepAliveRaf === 0) return;
      scene.requestRender();
      this.keepAliveRaf = requestAnimationFrame(keep);
    };
    this.keepAliveRaf = requestAnimationFrame(keep);
    let settled = false;
    const settle = (completed: boolean): void => {
      if (settled) return; // complete/cancel are mutually exclusive, but be exact
      settled = true;
      this.stopKeepAlive();
      done(completed);
    };
    camera.flyTo({
      destination,
      duration: FLY_SECONDS,
      easingFunction: EasingFunction.CUBIC_IN_OUT,
      complete: () => settle(true),
      cancel: () => settle(false),
    });
  }

  private stopKeepAlive(): void {
    if (this.keepAliveRaf !== 0) {
      cancelAnimationFrame(this.keepAliveRaf);
      this.keepAliveRaf = 0;
    }
  }

  /**
   * Stream the hero centre's window position to `callback` after every
   * rendered frame (null when the manifest has no hero). Returns a remover.
   */
  onAnchor(callback: (anchor: HeroAnchor | null) => void): () => void {
    const { scene, camera } = this.widget;
    return scene.postRender.addEventListener(() => {
      if (!this.heroCentre) {
        callback(null);
        return;
      }
      this.occluder.cameraPosition = camera.positionWC;
      // The place label's hero-view pin: the top-edge midpoint, projected
      // independently of the centre so the label can hug the box at hero zoom.
      let top: { x: number; y: number } | null = null;
      if (this.heroTopCentre !== null && this.occluder.isPointVisible(this.heroTopCentre)) {
        const w = SceneTransforms.worldToWindowCoordinates(
          scene,
          this.heroTopCentre,
          this.topScratch,
        );
        if (w !== undefined) top = { x: w.x, y: w.y };
      }
      const visible = this.occluder.isPointVisible(this.heroCentre);
      if (!visible) {
        callback({ x: 0, y: 0, visible: false, top });
        return;
      }
      const win = SceneTransforms.worldToWindowCoordinates(
        scene,
        this.heroCentre,
        this.anchorScratch,
      );
      if (win === undefined) {
        callback({ x: 0, y: 0, visible: false, top });
        return;
      }
      callback({ x: win.x, y: win.y, visible: true, top });
    });
  }

  destroy(): void {
    this.destroyed = true;
    this.stopIdleSpin();
    this.stopKeepAlive();
  }
}
