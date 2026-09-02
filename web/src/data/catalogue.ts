/**
 * Typed loader for the EVENT CATALOGUE — the small index that sits beside the
 * manifests and tells the app which hero events exist.
 *
 *   /data/web/catalogue.json — schema/catalogue.schema.json
 *   { schemaVersion: 1, events: [ { id, title, subtitle, manifest,
 *                                   kind, region, hasHero, default } ] }
 *
 * Two rules govern everything in this file.
 *
 * 1. THE CATALOGUE IS OPTIONAL. The site shipped, and still deploys, with a
 *    single manifest and no catalogue at all. A missing or unreadable
 *    catalogue must degrade to that behaviour, loudly logged, never a blank
 *    globe — see boot.ts.
 *
 * 2. THE MANIFEST IS THE AUTHORITY. The catalogue carries display copy and a
 *    pointer; it never decides what renders. `hasHero` in particular is a hint
 *    only: the app derives hero availability from the loaded manifest's layers
 *    (store.svelte.ts `heroAvailable`), so a stale catalogue can never make the
 *    UI claim a hero the data does not contain.
 *
 * Entries are therefore parsed permissively — a malformed entry is dropped with
 * a warning rather than taking the whole catalogue down — while the catalogue
 * as a whole is validated strictly enough that a wrong-shaped file is reported
 * rather than half-rendered.
 */

/** Default location, sibling of the manifests it points at. */
export const DEFAULT_CATALOGUE_URL = "/data/web/catalogue.json";

/** schema/catalogue.schema.json `kind` — derived by the pipeline from the manifest. */
export type EventKind = "global-only" | "hero";
/** schema/catalogue.schema.json `region` — a grouping key, not a bounding box. */
export type EventRegion = "global" | "taiwan" | "conus";

/**
 * Display names for the region KEYS. The catalogue's `region` is a closed enum
 * for grouping, not prose, so it is never rendered raw — it only stands in when
 * an entry somehow arrives without the (schema-required) subtitle.
 */
const REGION_LABELS: Record<EventRegion, string> = {
  global: "Global",
  taiwan: "Taiwan",
  conus: "Central US",
};

export interface CatalogueEvent {
  /** Stable kebab-case slug — this is what `?event=` carries. */
  id: string;
  /** Primary display name, e.g. "Global — Typhoon Gaemi week". */
  title: string;
  /** Secondary line under the title in the switcher. Schema-required; defended anyway. */
  subtitle?: string;
  /** Absolute URL of this event's manifest, resolved against the catalogue URL. */
  manifestUrl: string;
  /** What the event can show. HINT ONLY — see hasHero. Unknown values parse to undefined. */
  kind?: EventKind;
  /** Grouping key. Unknown values parse to undefined rather than reaching the UI. */
  region?: EventRegion;
  /** HINT ONLY — the loaded manifest decides whether a hero exists. */
  hasHero?: boolean;
  /** True for the entry marked `default` in the file. */
  isDefault: boolean;
}

export interface Catalogue {
  schemaVersion: 1;
  /** Absolute URL the catalogue was read from — manifest paths resolve against it. */
  url: string;
  events: CatalogueEvent[];
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function optionalString(v: unknown): string | undefined {
  return typeof v === "string" && v.length > 0 ? v : undefined;
}

/**
 * Parse a catalogue document. Throws on a document-level violation (wrong
 * schemaVersion, no events array, every entry unusable); drops individual
 * malformed entries with a console warning naming the index.
 */
export function parseCatalogue(raw: unknown, baseUrl: URL): Catalogue {
  if (!isRecord(raw)) throw new Error("catalogue invalid at $: expected an object");
  if (raw.schemaVersion !== 1) {
    throw new Error(
      `catalogue invalid at $.schemaVersion: expected 1, got ${JSON.stringify(raw.schemaVersion)}`,
    );
  }
  if (!Array.isArray(raw.events)) throw new Error("catalogue invalid at $.events: expected an array");

  const events: CatalogueEvent[] = [];
  const seen = new Set<string>();

  raw.events.forEach((entry: unknown, i: number) => {
    const path = `$.events[${i}]`;
    if (!isRecord(entry)) {
      console.warn(`[latent-sky] catalogue: dropping ${path} — not an object`);
      return;
    }
    const id = optionalString(entry.id);
    const manifest = optionalString(entry.manifest);
    if (id === undefined || manifest === undefined) {
      console.warn(
        `[latent-sky] catalogue: dropping ${path} — id and manifest are both required ` +
          `(id=${JSON.stringify(entry.id)}, manifest=${JSON.stringify(entry.manifest)})`,
      );
      return;
    }
    if (seen.has(id)) {
      console.warn(`[latent-sky] catalogue: dropping ${path} — duplicate id ${JSON.stringify(id)}`);
      return;
    }
    let manifestUrl: string;
    try {
      manifestUrl = new URL(manifest, baseUrl).toString();
    } catch (err: unknown) {
      console.warn(`[latent-sky] catalogue: dropping ${path} — unresolvable manifest path`, err);
      return;
    }
    seen.add(id);
    // Closed enums: anything the app does not recognise becomes undefined
    // rather than reaching the UI, so a catalogue written against a newer
    // schema degrades to "no hint" instead of rendering a raw key.
    const kind =
      entry.kind === "hero" || entry.kind === "global-only" ? (entry.kind as EventKind) : undefined;
    const region =
      entry.region === "global" || entry.region === "taiwan" || entry.region === "conus"
        ? (entry.region as EventRegion)
        : undefined;
    events.push({
      id,
      // A missing title is display-only damage: fall back to the id rather than
      // dropping an otherwise usable event.
      title: optionalString(entry.title) ?? id,
      subtitle:
        optionalString(entry.subtitle) ?? (region !== undefined ? REGION_LABELS[region] : undefined),
      manifestUrl,
      kind,
      region,
      hasHero: typeof entry.hasHero === "boolean" ? entry.hasHero : undefined,
      isDefault: entry.default === true,
    });
  });

  if (events.length === 0) throw new Error("catalogue invalid at $.events: no usable entries");
  return { schemaVersion: 1, url: baseUrl.toString(), events };
}

/**
 * Fetch and parse the catalogue. Returns null — never throws — when the site
 * has no usable catalogue, so boot.ts can fall back to the single-manifest
 * behaviour the site shipped with.
 *
 * Three outcomes, and the split between them is deliberate:
 *
 *   · unreachable, or HTTP >= 400 → ABSENT. warn. Expected: this is the
 *     deployment before a catalogue exists.
 *   · HTTP 200 with a body that is not JSON → ALSO ABSENT. warn, not error.
 *     A Vite dev server, and any SPA-style CDN error mapping, answers an
 *     unknown path with index.html and HTTP 200 (Architecture.md §10 records
 *     the same trap for Cesium's runtime assets, where it presented as an
 *     unrelated-looking RuntimeError). "200 text/html" therefore means the
 *     file is not there, and treating it as a defect would put a red error in
 *     the console of a perfectly healthy pre-catalogue deployment.
 *   · valid JSON of the wrong shape → A GENUINE DEFECT. error, with the
 *     violation named, and still a fallback rather than a dead globe.
 */
export async function loadCatalogue(url: string): Promise<Catalogue | null> {
  let response: Response;
  try {
    response = await fetch(url);
  } catch (err: unknown) {
    console.warn(
      `[latent-sky] catalogue unreachable at ${url} — falling back to the single manifest.`,
      err,
    );
    return null;
  }
  if (!response.ok) {
    console.warn(
      `[latent-sky] no catalogue at ${url} (HTTP ${response.status}) — ` +
        "falling back to the single manifest. This is the expected pre-catalogue deployment.",
    );
    return null;
  }

  const body = await response.text();
  let raw: unknown;
  try {
    raw = JSON.parse(body) as unknown;
  } catch {
    console.warn(
      `[latent-sky] no catalogue at ${url} — HTTP ${response.status} but the body is not JSON ` +
        `(content-type ${response.headers.get("content-type") ?? "unknown"}, ${body.length} B). ` +
        "A dev server or CDN SPA fallback answers unknown paths with index.html and 200, so this " +
        "is the same 'no catalogue' case as a 404. Falling back to the single manifest.",
    );
    return null;
  }

  try {
    return parseCatalogue(raw, new URL(url, window.location.href));
  } catch (err: unknown) {
    console.error(
      `[latent-sky] the catalogue at ${url} is valid JSON but does not match ` +
        "schema/catalogue.schema.json — falling back to the single manifest. The event switcher " +
        "stays disabled until it parses.",
      err,
    );
    return null;
  }
}

/** `?catalogue=` override (dev fixtures, previews) else the shipped location. */
export function catalogueUrlFromLocation(loc: Location = window.location): string {
  return new URLSearchParams(loc.search).get("catalogue") ?? DEFAULT_CATALOGUE_URL;
}

/** `?event=` — the linkable event selection, or null. */
export function eventIdFromLocation(loc: Location = window.location): string | null {
  const id = new URLSearchParams(loc.search).get("event");
  return id !== null && id.length > 0 ? id : null;
}

/**
 * Resolve which event to open: the `?event=` one when it exists, else the entry
 * flagged `default`, else the first. An unknown `?event=` is a broken shared
 * link — say so plainly and open the default rather than showing nothing.
 */
export function chooseEvent(catalogue: Catalogue, requestedId: string | null): CatalogueEvent {
  if (requestedId !== null) {
    const requested = catalogue.events.find((e) => e.id === requestedId);
    if (requested) return requested;
    console.warn(
      `[latent-sky] ?event=${requestedId} is not in the catalogue ` +
        `(have: ${catalogue.events.map((e) => e.id).join(", ")}) — opening the default event.`,
    );
  }
  return catalogue.events.find((e) => e.isDefault) ?? catalogue.events[0];
}

/**
 * Write the active event into `?event=` with history.replaceState, so the
 * address bar is always a link straight to what is on screen. No-op for a
 * single-event catalogue unless the param is already present (then it is kept
 * accurate rather than left stale).
 */
export function writeEventToUrl(catalogue: Catalogue, id: string, loc: Location = window.location): void {
  const url = new URL(loc.href);
  if (catalogue.events.length < 2 && !url.searchParams.has("event")) return;
  if (url.searchParams.get("event") === id) return;
  url.searchParams.set("event", id);
  history.replaceState(history.state, "", url.toString());
}
