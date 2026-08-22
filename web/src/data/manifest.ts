/**
 * Typed loader for the data manifest — THE contract between pipeline/ and web/
 * (schema/manifest.schema.json). Lightweight structural validation, not full
 * jsonschema: every check throws with the JSON path of the violation, because a
 * malformed manifest must fail loudly, never render half a globe.
 */

export type Variable = "wind10m" | "t2m" | "mrr" | "tcwv" | "msl";
export type LayerKind = "global" | "hero-fine" | "hero-coarse";

const VARIABLES: readonly Variable[] = ["wind10m", "t2m", "mrr", "tcwv", "msl"];
const KINDS: readonly LayerKind[] = ["global", "hero-fine", "hero-coarse"];

export interface RunInfo {
  id: string;
  kind: "forecast" | "dev-sample";
  init?: string;
  model: { prognostic: string; downscaling: string };
  generatedNote: string;
  /** First-class storm name, e.g. "Typhoon Gaemi". Absent when no storm claim
   * should be made — use stormNameFor() for the heuristic fallback. */
  stormName?: string;
  /** Index into frameIso of the most developed frame — where "enter the storm"
   * should arrive. Use heroFrameFor() for the last-frame fallback. */
  heroFrame?: number;
  /** Human label for the hero domain, e.g. "Taiwan · CWA model domain". */
  placeLabel?: string;
}

export interface LayerDef {
  id: string;
  kind: LayerKind;
  variable: Variable;
  label: string;
  units: string;
  /** [west, south, east, north] degrees */
  rect: [number, number, number, number];
  /** [width, height] pixels */
  size: [number, number];
  /** Absolute URL of the shared 256x1 RGBA LUT PNG */
  lutUrl: string;
  vmin: number;
  vmax: number;
  identity: string;
  /** Absolute URL per frame, same length and order as Manifest.frameIso */
  frameUrls: string[];
  pairWith?: string;
}

/**
 * Optional offline-baked basemap imagery (schema `basemap`). URLs are resolved
 * against the manifest URL, exactly like layer frames. Arrives pre-styled —
 * §7.2(c): the app never touches brightness/contrast/hue/saturation/gamma.
 */
export interface BasemapDef {
  /** Absolute URL of the full-globe equirectangular basemap, if declared. */
  globalUrl?: string;
  /** [west, south, east, north] degrees; defaults to the full globe. */
  globalRect: [number, number, number, number];
  /** Absolute URL of the crisp regional basemap over the hero rectangle, if declared. */
  heroUrl?: string;
  heroRect?: [number, number, number, number];
}

export interface Manifest {
  schemaVersion: 1;
  run: RunInfo;
  /** Valid times as ISO strings, strictly increasing */
  frameIso: string[];
  basemap?: BasemapDef;
  layers: Map<string, LayerDef>;
}

export function manifestUrlFromLocation(loc: Location = window.location): string {
  return new URLSearchParams(loc.search).get("manifest") ?? "/data/web/manifest.json";
}

function fail(path: string, message: string): never {
  throw new Error(`manifest invalid at ${path}: ${message}`);
}

function requireString(v: unknown, path: string): string {
  if (typeof v !== "string" || v.length === 0) fail(path, `expected non-empty string, got ${JSON.stringify(v)}`);
  return v;
}

function requireNumber(v: unknown, path: string): number {
  if (typeof v !== "number" || !Number.isFinite(v)) fail(path, `expected finite number, got ${JSON.stringify(v)}`);
  return v;
}

function requireRecord(v: unknown, path: string): Record<string, unknown> {
  if (typeof v !== "object" || v === null || Array.isArray(v)) fail(path, "expected object");
  return v as Record<string, unknown>;
}

function requireIndex(v: unknown, path: string): number {
  const n = requireNumber(v, path);
  if (!Number.isInteger(n) || n < 0) fail(path, `expected non-negative integer index, got ${n}`);
  return n;
}

function requireIso(v: unknown, path: string): string {
  const s = requireString(v, path);
  if (Number.isNaN(Date.parse(s))) fail(path, `not a parseable date-time: ${s}`);
  return s;
}

function requireRect(v: unknown, path: string): [number, number, number, number] {
  if (!Array.isArray(v) || v.length !== 4) fail(path, "expected [west, south, east, north]");
  const rect = v.map((n, i) => requireNumber(n, `${path}[${i}]`)) as [number, number, number, number];
  if (!(rect[0] < rect[2] && rect[1] < rect[3])) fail(path, `degenerate rectangle ${JSON.stringify(rect)}`);
  return rect;
}

export async function loadManifest(url: string): Promise<Manifest> {
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`manifest fetch failed: ${response.status} ${response.statusText} for ${url}`);
  }
  const raw: unknown = await response.json();
  const baseUrl = new URL(url, window.location.href);
  return parseManifest(raw, baseUrl);
}

export function parseManifest(raw: unknown, baseUrl: URL): Manifest {
  const root = requireRecord(raw, "$");
  if (root.schemaVersion !== 1) fail("$.schemaVersion", `expected 1, got ${JSON.stringify(root.schemaVersion)}`);

  const runRaw = requireRecord(root.run, "$.run");
  const kind = requireString(runRaw.kind, "$.run.kind");
  if (kind !== "forecast" && kind !== "dev-sample") fail("$.run.kind", `unknown kind ${kind}`);
  const modelRaw = requireRecord(runRaw.model, "$.run.model");
  const run: RunInfo = {
    id: requireString(runRaw.id, "$.run.id"),
    kind,
    init: runRaw.init === undefined ? undefined : requireIso(runRaw.init, "$.run.init"),
    model: {
      prognostic: requireString(modelRaw.prognostic, "$.run.model.prognostic"),
      downscaling: requireString(modelRaw.downscaling, "$.run.model.downscaling"),
    },
    generatedNote: requireString(runRaw.generatedNote, "$.run.generatedNote"),
    stormName: runRaw.stormName === undefined ? undefined : requireString(runRaw.stormName, "$.run.stormName"),
    heroFrame: runRaw.heroFrame === undefined ? undefined : requireIndex(runRaw.heroFrame, "$.run.heroFrame"),
    placeLabel: runRaw.placeLabel === undefined ? undefined : requireString(runRaw.placeLabel, "$.run.placeLabel"),
  };

  if (!Array.isArray(root.frames) || root.frames.length < 1) fail("$.frames", "expected non-empty array");
  const frameIso = root.frames.map((f, i) => requireIso(f, `$.frames[${i}]`));
  for (let i = 1; i < frameIso.length; i++) {
    if (Date.parse(frameIso[i]) <= Date.parse(frameIso[i - 1])) {
      fail(`$.frames[${i}]`, `not strictly increasing: ${frameIso[i - 1]} -> ${frameIso[i]}`);
    }
  }
  // heroFrame indexes into frames — checkable only once both are parsed.
  if (run.heroFrame !== undefined && run.heroFrame >= frameIso.length) {
    fail("$.run.heroFrame", `index ${run.heroFrame} out of range for ${frameIso.length} frames`);
  }

  let basemap: BasemapDef | undefined;
  if (root.basemap !== undefined) {
    const b = requireRecord(root.basemap, "$.basemap");
    basemap = {
      globalUrl:
        b.global === undefined
          ? undefined
          : new URL(requireString(b.global, "$.basemap.global"), baseUrl).toString(),
      globalRect:
        b.globalRect === undefined ? [-180, -90, 180, 90] : requireRect(b.globalRect, "$.basemap.globalRect"),
      heroUrl:
        b.hero === undefined
          ? undefined
          : new URL(requireString(b.hero, "$.basemap.hero"), baseUrl).toString(),
      heroRect: b.heroRect === undefined ? undefined : requireRect(b.heroRect, "$.basemap.heroRect"),
    };
  }

  const layersRaw = requireRecord(root.layers, "$.layers");
  const ids = Object.keys(layersRaw);
  if (ids.length < 1) fail("$.layers", "expected at least one layer");

  const layers = new Map<string, LayerDef>();
  for (const id of ids) {
    const p = `$.layers[${JSON.stringify(id)}]`;
    const l = requireRecord(layersRaw[id], p);

    const layerKind = requireString(l.kind, `${p}.kind`) as LayerKind;
    if (!KINDS.includes(layerKind)) fail(`${p}.kind`, `unknown kind ${layerKind}`);
    const variable = requireString(l.variable, `${p}.variable`) as Variable;
    if (!VARIABLES.includes(variable)) fail(`${p}.variable`, `unknown variable ${variable}`);

    const rect = requireRect(l.rect, `${p}.rect`);

    if (!Array.isArray(l.size) || l.size.length !== 2) fail(`${p}.size`, "expected [width, height]");
    const size = l.size.map((v, i) => {
      const n = requireNumber(v, `${p}.size[${i}]`);
      if (!Number.isInteger(n) || n < 1) fail(`${p}.size[${i}]`, `expected positive integer, got ${n}`);
      return n;
    }) as [number, number];

    const vmin = requireNumber(l.vmin, `${p}.vmin`);
    const vmax = requireNumber(l.vmax, `${p}.vmax`);
    if (!(vmin < vmax)) fail(`${p}.vmin`, `vmin ${vmin} must be < vmax ${vmax}`);

    if (!Array.isArray(l.frames) || l.frames.length !== frameIso.length) {
      fail(`${p}.frames`, `expected ${frameIso.length} frame paths (one per $.frames entry), got ${Array.isArray(l.frames) ? l.frames.length : typeof l.frames}`);
    }
    const frameUrls = l.frames.map((f, i) => new URL(requireString(f, `${p}.frames[${i}]`), baseUrl).toString());

    layers.set(id, {
      id,
      kind: layerKind,
      variable,
      label: requireString(l.label, `${p}.label`),
      units: requireString(l.units, `${p}.units`),
      rect,
      size,
      lutUrl: new URL(requireString(l.lut, `${p}.lut`), baseUrl).toString(),
      vmin,
      vmax,
      identity: requireString(l.identity, `${p}.identity`),
      frameUrls,
      pairWith: l.pairWith === undefined ? undefined : requireString(l.pairWith, `${p}.pairWith`),
    });
  }

  // Cross-references: pairWith must resolve, and a pair must share its variable.
  for (const [id, layer] of layers) {
    if (layer.pairWith !== undefined) {
      const other = layers.get(layer.pairWith);
      if (!other) fail(`$.layers[${JSON.stringify(id)}].pairWith`, `no such layer ${layer.pairWith}`);
      if (other.variable !== layer.variable) {
        fail(`$.layers[${JSON.stringify(id)}].pairWith`, `pairs ${layer.variable} with ${other.variable} — a reveal must compare the same variable`);
      }
    }
  }

  return { schemaVersion: 1, run, frameIso, basemap, layers };
}

/**
 * The storm's display name. Prefers the first-class run.stormName; falls back
 * to the legacy heuristic (parsing "Typhoon X" out of generatedNote), then to
 * the honest non-claim "the hero region". UI adoption point for the invitation.
 */
export function stormNameFor(manifest: Manifest): string {
  return (
    manifest.run.stormName ??
    /Typhoon\s+[A-Z][a-z]+/.exec(manifest.run.generatedNote)?.[0] ??
    "the hero region"
  );
}

/**
 * The frame index "enter the storm" should arrive on. Prefers the first-class
 * run.heroFrame (already range-checked at parse); falls back to the legacy
 * heuristic — the last frame, on the grounds that frames are chronological and
 * quiet early frames must never sit under the invitation copy.
 */
export function heroFrameFor(manifest: Manifest): number {
  return manifest.run.heroFrame ?? manifest.frameIso.length - 1;
}

/**
 * Human label for the hero domain, e.g. "Taiwan · CWA model domain", or
 * undefined when the manifest makes no claim — there is no heuristic to fall
 * back to, and inventing a place would be exactly the failure the first-class
 * field exists to prevent.
 */
export function placeLabelFor(manifest: Manifest): string | undefined {
  return manifest.run.placeLabel;
}

/** The hero-fine layer for a variable, or undefined. */
export function heroFineLayerFor(manifest: Manifest, variable: Variable): LayerDef | undefined {
  for (const layer of manifest.layers.values()) {
    if (layer.variable === variable && layer.kind === "hero-fine") return layer;
  }
  return undefined;
}

/** The kind-"global" layer for a variable, or undefined. */
export function globalLayerFor(manifest: Manifest, variable: Variable): LayerDef | undefined {
  for (const layer of manifest.layers.values()) {
    if (layer.variable === variable && layer.kind === "global") return layer;
  }
  return undefined;
}

/** The hero-fine (falling back to global) layer for a variable, or undefined. */
export function primaryLayerFor(manifest: Manifest, variable: Variable): LayerDef | undefined {
  return heroFineLayerFor(manifest, variable) ?? globalLayerFor(manifest, variable);
}

/** Variables this manifest can actually render, in schema enum order. */
export function availableVariables(manifest: Manifest): Variable[] {
  return VARIABLES.filter((v) => primaryLayerFor(manifest, v) !== undefined);
}
