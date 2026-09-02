/**
 * Widget construction — Architecture.md §6.4, §7.2(c), §9.5.
 *
 * Framework-free: Svelte never touches anything constructed here.
 * Zero Cesium ion: empty token, ellipsoid terrain, no sky box. The base layer
 * is the manifest's offline-baked global basemap when one is declared
 * (pre-styled, served alongside the data); the bundled public-domain Natural
 * Earth II TMS is the fallback for manifests without one. Either way, zero
 * requests leave the deployment origin. The lost atmospheric halo is replaced
 * by SkyAtmosphere, which is a separate object and never touches imagery.
 *
 * The widget outlives any one manifest. Event switching re-initialises every
 * per-manifest object (base layer, timeline, renderer, camera director) against
 * the SAME widget rather than rebuilding it, because CesiumWidget.destroy()
 * never calls WEBGL_lose_context (verified in the 1.143.0 build) — a widget per
 * switch would accumulate live WebGL contexts until the browser force-loses the
 * oldest, which on a switch-heavy session means losing the visible globe. So
 * the base layer is NOT constructed here: `baseLayer: false`, and each session
 * adds and removes its own at collection index 0.
 */
import {
  CesiumWidget,
  Color,
  EllipsoidTerrainProvider,
  ImageryLayer,
  Ion,
  Rectangle,
  SingleTileImageryProvider,
  SkyAtmosphere,
  TileMapServiceImageryProvider,
  buildModuleUrl,
} from "cesium";
import type { BasemapDef } from "../data/manifest";

export interface WidgetOptions {
  /** Enable preserveDrawingBuffer so tests can read pixels back. Off in production. */
  pixelReadback?: boolean;
}

/**
 * The session's base imagery layer: the manifest's offline-baked global
 * basemap, or the bundled Natural Earth II TMS when a manifest declares none.
 * Owned by the session (globe/index.ts), not the widget — a switched-to event
 * may ship a different basemap, and a stale one must never survive the switch.
 */
export function createBaseLayer(basemap?: BasemapDef): ImageryLayer {
  if (basemap?.globalUrl !== undefined) {
    const [west, south, east, north] = basemap.globalRect;
    return ImageryLayer.fromProviderAsync(
      SingleTileImageryProvider.fromUrl(basemap.globalUrl, {
        rectangle: Rectangle.fromDegrees(west, south, east, north),
      }),
      {},
      // No filter/colour options: the basemap arrives pre-styled — §7.2(c),
      // never touch brightness/contrast/hue/saturation/gamma on any layer.
    );
  }
  return ImageryLayer.fromProviderAsync(
    TileMapServiceImageryProvider.fromUrl(buildModuleUrl("Assets/Textures/NaturalEarthII")),
    {},
  );
}

export function createWidget(container: HTMLElement, options: WidgetOptions = {}): CesiumWidget {
  Ion.defaultAccessToken = ""; // §9.5 — zero ion, zero quota, zero api.cesium.com traffic

  const widget = new CesiumWidget(container, {
    // false, not undefined: undefined defaults baseLayer to ImageryLayer.fromWorldImagery(),
    // which is an ion asset (§9.5). The session adds the real base layer at index 0.
    baseLayer: false,
    terrainProvider: new EllipsoidTerrainProvider(),
    skyBox: false, // also drops 864 KB of star JPEGs a weather globe does not need
    skyAtmosphere: new SkyAtmosphere(),
    requestRenderMode: true,
    maximumRenderTimeChange: Infinity,
    contextOptions: options.pixelReadback ? { webgl: { preserveDrawingBuffer: true } } : undefined,
  });

  const scene = widget.scene;
  const globe = scene.globe;

  scene.msaaSamples = 1; // default is 4 (measured, Probe 3) — integrated graphics budget

  // §7.2(c) — these four lines are load-bearing
  globe.showGroundAtmosphere = false; // else fade≈0.28 at globe zoom vs 0.0 at hero zoom
  scene.fog.enabled = false; // hits the near field only — asymmetric by construction
  console.assert(!scene.highDynamicRange, "HDR must be off: czm_gammaCorrect must stay a no-op");
  console.assert(!globe.enableLighting, "globe lighting must be off: imagery must render authored bytes");

  // Never touch brightness / contrast / hue / saturation / gamma on any layer.
  // Filters are immutable once a texture loads — layers.ts sets them at construction.

  globe.baseColor = Color.fromCssColorString("#070c1a"); // the basemap's ocean — one palette

  // Debug back-reference for DevTools and the smoke-test probes. Not an API.
  (container as HTMLElement & { cesiumWidget?: CesiumWidget }).cesiumWidget = widget;

  return widget;
}
