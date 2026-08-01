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

export interface Manifest {
  schemaVersion: 1;
  run: RunInfo;
  /** Valid times as ISO strings, strictly increasing */
  frameIso: string[];
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

function requireIso(v: unknown, path: string): string {
  const s = requireString(v, path);
  if (Number.isNaN(Date.parse(s))) fail(path, `not a parseable date-time: ${s}`);
  return s;
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
  };

  if (!Array.isArray(root.frames) || root.frames.length < 1) fail("$.frames", "expected non-empty array");
  const frameIso = root.frames.map((f, i) => requireIso(f, `$.frames[${i}]`));
  for (let i = 1; i < frameIso.length; i++) {
    if (Date.parse(frameIso[i]) <= Date.parse(frameIso[i - 1])) {
      fail(`$.frames[${i}]`, `not strictly increasing: ${frameIso[i - 1]} -> ${frameIso[i]}`);
    }
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

    if (!Array.isArray(l.rect) || l.rect.length !== 4) fail(`${p}.rect`, "expected [west, south, east, north]");
    const rect = l.rect.map((v, i) => requireNumber(v, `${p}.rect[${i}]`)) as [number, number, number, number];
    if (!(rect[0] < rect[2] && rect[1] < rect[3])) fail(`${p}.rect`, `degenerate rectangle ${JSON.stringify(rect)}`);

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

  return { schemaVersion: 1, run, frameIso, layers };
}

/** The hero-fine (falling back to global) layer for a variable, or undefined. */
export function primaryLayerFor(manifest: Manifest, variable: Variable): LayerDef | undefined {
  let global: LayerDef | undefined;
  for (const layer of manifest.layers.values()) {
    if (layer.variable !== variable) continue;
    if (layer.kind === "hero-fine") return layer;
    if (layer.kind === "global") global ??= layer;
  }
  return global;
}

/** Variables this manifest can actually render, in schema enum order. */
export function availableVariables(manifest: Manifest): Variable[] {
  return VARIABLES.filter((v) => primaryLayerFor(manifest, v) !== undefined);
}
