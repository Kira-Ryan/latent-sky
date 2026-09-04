/**
 * Payload gate — TRANSFER bytes, per Architecture.md §8.1.
 *
 * `du -sb dist` measures the wrong thing: dist/ is 6+ MB raw before any data
 * because Cesium.js is 3.75 MB raw but 0.75 MB brotli. Delivery pre-compresses
 * at upload with explicit Content-Encoding (§9.2), so the honest metric is what
 * a visitor is actually served:
 *
 *   - compressible assets (js/css/html/json/svg/xml/wasm/…) at brotli q11
 *   - already-compressed assets (webp/png/ktx2/glb/woff2/…) at raw size
 *
 * summed over everything servable: web/dist plus data/web (the committed data
 * artefact) when it exists. data/web does not exist until the GPU run ships;
 * until then the gate covers the app payload alone and says so.
 *
 * Conservative by construction: it includes ALL of dist/cesium even though a
 * real session fetches only a fraction of it, and counts unknown extensions raw.
 * Decimal MB throughout (1 MB = 1,000,000 B), matching every figure in §8.
 *
 *   node tools/transfer-budget.mjs [--dist dist] [--data ../data/web] [--ceiling-mb 12]
 *
 * Exit 0 within budget, 1 over budget, 2 usage error / missing dist.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { brotliCompressSync, constants as zc } from "node:zlib";

const WEB = dirname(dirname(fileURLToPath(import.meta.url))); // web/
const MB = 1_000_000;

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(name);
  return i >= 0 && args[i + 1] !== undefined ? args[i + 1] : fallback;
};
const DIST = resolve(WEB, opt("--dist", "dist"));
const DATA = resolve(WEB, opt("--data", join("..", "data", "web")));
const CEILING_MB = Number(opt("--ceiling-mb", "12"));
if (!Number.isFinite(CEILING_MB) || CEILING_MB <= 0) {
  console.error(`invalid --ceiling-mb: ${opt("--ceiling-mb", "12")}`);
  process.exit(2);
}
if (!existsSync(DIST)) {
  console.error(`dist not found at ${DIST} — run \`npm run build\` first`);
  process.exit(2);
}

// Pre-compressed at upload, served with Content-Encoding: br (§9.2).
const COMPRESSIBLE = new Set([
  ".js", ".mjs", ".css", ".html", ".json", ".svg", ".xml", ".txt", ".md", ".map", ".wasm",
]);
// Already-compressed formats — shipped byte-for-byte.
const RAW = new Set([
  ".webp", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".ktx2", ".glb", ".woff2", ".terrain", ".br", ".gz",
]);

const brotli = (buf) =>
  brotliCompressSync(buf, {
    params: {
      [zc.BROTLI_PARAM_QUALITY]: 11,
      [zc.BROTLI_PARAM_SIZE_HINT]: buf.length,
    },
  }).length;

const unknownExts = new Set();

/** Recursively measure a directory: [fileCount, rawBytes, transferBytes]. */
function measure(dir) {
  let files = 0, raw = 0, transfer = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    const p = join(dir, entry.name);
    if (entry.isDirectory()) {
      const [f, r, t] = measure(p);
      files += f; raw += r; transfer += t;
    } else {
      const ext = extname(entry.name).toLowerCase();
      const size = statSync(p).size;
      files += 1;
      raw += size;
      if (COMPRESSIBLE.has(ext)) {
        transfer += brotli(readFileSync(p));
      } else {
        if (!RAW.has(ext)) unknownExts.add(ext || "(none)");
        transfer += size; // unknown extension: counted raw — conservative
      }
    }
  }
  return [files, raw, transfer];
}

/** One report row per logical group. */
const groups = [];
const addGroup = (label, dir, exclude = new Set()) => {
  let files = 0, raw = 0, transfer = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
    if (exclude.has(entry.name)) continue;
    const p = join(dir, entry.name);
    const [f, r, t] = entry.isDirectory() ? measure(p) : measure_file(p);
    files += f; raw += r; transfer += t;
  }
  groups.push({ label, files, raw, transfer });
};
const measure_file = (p) => {
  const ext = extname(p).toLowerCase();
  const size = statSync(p).size;
  if (COMPRESSIBLE.has(ext)) return [1, size, brotli(readFileSync(p))];
  if (!RAW.has(ext)) unknownExts.add(ext || "(none)");
  return [1, size, size];
};

addGroup("app (dist, excl. cesium static)", DIST, new Set(["cesium"]));
if (existsSync(join(DIST, "cesium"))) {
  addGroup("cesium static (dist/cesium — all of it; a session fetches less)", join(DIST, "cesium"));
}

// WHAT A VISITOR ACTUALLY FETCHES, which is the app plus ONE event — not every
// event on the site. The budget was written when there was a single event and
// summing data/web was the same thing; once a catalogue landed it stopped being,
// and the gate began charging one visitor for four other events they will never
// open. So each event is measured by resolving its own manifest — the exact files
// the browser requests — rather than by guessing from directory names, which
// mis-attributes the shared basemap and the global event's layer tree.
const resolveEvent = (manifestRel) => {
  const mPath = join(DATA, manifestRel);
  if (!existsSync(mPath)) return null;
  const m = JSON.parse(readFileSync(mPath, "utf8"));
  const base = dirname(mPath);
  const rels = new Set();
  for (const layer of Object.values(m.layers ?? {})) {
    for (const f of layer.frames ?? []) rels.add(f);
    if (layer.lut) rels.add(layer.lut);
  }
  for (const k of ["global", "hero"]) if (m.basemap?.[k]) rels.add(m.basemap[k]);
  let files = 1, raw = 0, transfer = 0;
  const [, mr, mt] = measure_file(mPath);
  raw += mr; transfer += mt;
  for (const rel of rels) {
    const p = join(base, rel);
    if (!existsSync(p)) continue;
    const [f, r, t] = measure_file(p);
    files += f; raw += r; transfer += t;
  }
  return { files, raw, transfer };
};

let dataPresent = false;
const events = [];
if (existsSync(DATA)) {
  dataPresent = true;
  const catPath = join(DATA, "catalogue.json");
  const [, cr, ct] = measure_file(catPath);
  if (existsSync(catPath)) groups.push({ label: "data/web/catalogue.json (every session)", files: 1, raw: cr, transfer: ct });
  const entries = existsSync(catPath)
    ? JSON.parse(readFileSync(catPath, "utf8")).events.map((e) => ({ id: e.id, manifest: e.manifest }))
    : [{ id: "manifest.json", manifest: "manifest.json" }];
  for (const e of entries) {
    const m = resolveEvent(e.manifest);
    if (m) events.push({ label: `event ${e.id}`, ...m });
  }
}

const heaviest = events.reduce((a, b) => (b.transfer > (a?.transfer ?? -1) ? b : a), null);
for (const e of events) {
  groups.push({ ...e, label: `${e.label}${e === heaviest ? "  <- worst case, counted" : "  (a session does not load this)"}` });
}

const counted = (g) => !g.label.endsWith("(a session does not load this)");
const total = groups.filter(counted).reduce((a, g) => a + g.transfer, 0);
const totalRaw = groups.filter(counted).reduce((a, g) => a + g.raw, 0);
const ceiling = Math.round(CEILING_MB * MB);

const W = Math.max(...groups.map((g) => g.label.length), "TOTAL (transfer)".length) + 2;
const fmt = (n) => (n / MB).toFixed(3).padStart(8);
console.log(`\nTransfer-byte payload budget (§8.1) — brotli q11 for compressible, raw for pre-compressed`);
console.log(`dist: ${relative(WEB, DIST)}   data: ${dataPresent ? DATA : "(absent)"}\n`);
console.log(`  ${"group".padEnd(W)} ${"files".padStart(5)} ${"raw MB".padStart(8)} ${"xfer MB".padStart(8)}`);
console.log("  " + "-".repeat(W + 24));
for (const g of groups) {
  console.log(`  ${g.label.padEnd(W)} ${String(g.files).padStart(5)} ${fmt(g.raw)} ${fmt(g.transfer)}`);
}
console.log("  " + "-".repeat(W + 24));
if (events.length > 1) {
  console.log(`  ${`(${events.length} events on the site; a session loads exactly one)`.padEnd(W)}`);
}
console.log(`  ${"TOTAL (worst-case session)".padEnd(W)} ${" ".repeat(5)} ${fmt(totalRaw)} ${fmt(total)}`);
console.log(`  ${"CEILING".padEnd(W)} ${" ".repeat(5)} ${" ".repeat(8)} ${fmt(ceiling)}`);

if (!dataPresent) {
  console.log(
    `\nNote: ${relative(resolve(WEB, ".."), DATA)} is absent — the GPU run has not shipped yet, so the gate covers the app payload only.`,
  );
}
if (unknownExts.size) {
  console.log(`Note: unknown extensions counted at raw size (conservative): ${[...unknownExts].sort().join(", ")}`);
}

if (total <= ceiling) {
  console.log(`\nWITHIN BUDGET — ${((ceiling - total) / MB).toFixed(3)} MB of headroom under the ${CEILING_MB} MB ceiling\n`);
  process.exit(0);
} else {
  console.log(`\nOVER BUDGET by ${((total - ceiling) / MB).toFixed(3)} MB — build must fail\n`);
  process.exit(1);
}
