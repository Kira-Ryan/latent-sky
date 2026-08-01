/**
 * Widget construction — Architecture.md §6.4, §7.2(c), §9.5.
 *
 * Framework-free: Svelte never touches anything constructed here.
 * Zero Cesium ion: empty token, bundled Natural Earth II base layer, ellipsoid
 * terrain, no sky box. The lost atmospheric halo is replaced by SkyAtmosphere,
 * which is a separate object and never touches imagery.
 */
import {
  CesiumWidget,
  Color,
  EllipsoidTerrainProvider,
  ImageryLayer,
  Ion,
  SkyAtmosphere,
  TileMapServiceImageryProvider,
  buildModuleUrl,
} from "cesium";

export interface WidgetOptions {
  /** Enable preserveDrawingBuffer so tests can read pixels back. Off in production. */
  pixelReadback?: boolean;
}

export function createWidget(container: HTMLElement, options: WidgetOptions = {}): CesiumWidget {
  Ion.defaultAccessToken = ""; // §9.5 — zero ion, zero quota, zero api.cesium.com traffic

  const widget = new CesiumWidget(container, {
    baseLayer: ImageryLayer.fromProviderAsync(
      TileMapServiceImageryProvider.fromUrl(buildModuleUrl("Assets/Textures/NaturalEarthII")),
      {},
    ),
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

  globe.baseColor = Color.fromCssColorString("#0b0e14"); // designed-quiet: the field is the visual

  // Debug back-reference for DevTools and the smoke-test probes. Not an API.
  (container as HTMLElement & { cesiumWidget?: CesiumWidget }).cesiumWidget = widget;

  return widget;
}
