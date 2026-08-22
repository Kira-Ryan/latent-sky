/**
 * OG image capture — LICENCE-CRITICAL (Architecture.md §3.10, §12).
 *
 * og.png must contain ZERO pixels derived from the CC BY-NC-ND hero layers
 * (the CorrDiff package's CWB/WRF-derived fields). Three gates enforce that:
 *
 *   1. STRUCTURAL — the capture selects tcwv, the global-only variable. The
 *      renderer's rebuildLayers() destroys the hero stacks outright on a
 *      variable switch, so NO hero ImageryLayer exists in the scene at capture
 *      time. What remains renderable is exactly the publishable stack: the
 *      dark basemap (Natural Earth, public domain) and the global ERA5 total
 *      column water vapour field (CC-BY).
 *   2. AUDIT — the live imagery collection is enumerated via the widget
 *      debug back-reference and the script FAILS unless it contains exactly
 *      basemap + one global stack, every layer's provider rectangle spanning
 *      the full globe. The hero rectangle is 9.4 degrees (0.164 rad) wide —
 *      a hero layer cannot pass this gate.
 *   3. CHROME — the invitation / hero marker / place label are hidden through
 *      the __latentSky.setHeroOverlay hook and the app chrome via injected
 *      CSS, so the only additions are the title treatment this script draws.
 *
 * Writes web/public/og.png at exactly 1200x630. Exits non-zero on any
 * console/page error or any failed gate.
 *
 *   PATH="/c/Users/User/AppData/Roaming/nvm/v22.21.1:$PATH" node tests/capture-og.mjs
 */
import { mkdir, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT = join(WEB, "public", "og.png");
const PORT = 8423;
const WIDTH = 1200;
const HEIGHT = 630;

await mkdir(join(WEB, "public"), { recursive: true });

const server = await createServer({
  root: WEB,
  server: { port: PORT, strictPort: true },
  logLevel: "warn",
});
await server.listen();

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT } });
const problems = [];
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "assert") problems.push(m.text());
});
page.on("pageerror", (e) => problems.push(String(e)));

// ?test=1 turns on preserveDrawingBuffer so the settle poll can read pixels.
await page.goto(`http://localhost:${PORT}/?manifest=/data/dev/encoded/manifest.json&test=1`, {
  waitUntil: "load",
});
await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, {
  timeout: 60_000,
});

// Stop the idle spin deterministically — same neutral tap as capture-real-data.
await page.mouse.move(100, 100);
await page.mouse.down();
await page.mouse.up();
await page.waitForTimeout(300);

/** Sample an even grid of RGB triples from the Cesium canvas. */
const sample = () =>
  page.evaluate(async () => {
    globalThis.__latentSky.requestRender();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const c = document.querySelector(".globe-wrap canvas");
    if (!c) throw new Error("no globe canvas found");
    const tmp = document.createElement("canvas");
    tmp.width = c.width;
    tmp.height = c.height;
    const ctx = tmp.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(c, 0, 0);
    const data = ctx.getImageData(0, 0, tmp.width, tmp.height).data;
    const step = 24;
    const out = [];
    for (let y = step >> 1; y < tmp.height; y += step) {
      for (let x = step >> 1; x < tmp.width; x += step) {
        const i = (y * tmp.width + x) * 4;
        out.push([data[i], data[i + 1], data[i + 2]]);
      }
    }
    return out;
  });

const before = await sample();

// GATE 1 (structural) + chrome: global-only variable, hero overlay off,
// Chanthu's moisture field (frame 3 = 2021-09-12 00Z, ERA5 — CC-BY).
await page.evaluate(() => {
  globalThis.__latentSky.setHeroOverlay(false);
  globalThis.__latentSky.setVariable("tcwv");
  globalThis.__latentSky.setFrame(3);
});

// Wait until the tcwv textures are decoded and composited (decode is off the
// frame path — poll rather than race it), then require two stable samples.
let last = await sample();
let settled = false;
for (let attempt = 0; attempt < 30; attempt++) {
  await page.waitForTimeout(300);
  const next = await sample();
  const changedFromBefore = before.filter((p, i) => p.join(",") !== next[i].join(",")).length;
  const stable = last.every((p, i) => p.join(",") === next[i].join(","));
  last = next;
  if (changedFromBefore > 5 && stable) {
    settled = true;
    break;
  }
}
if (!settled) problems.push("tcwv layer never settled — capture would race the decode");

// GATE 2 (audit): enumerate the live imagery collection. Fails unless it is
// exactly basemap + 3 tcwv slots, all spanning the full globe.
const audit = await page.evaluate(() => {
  const container = document.querySelector(".globe");
  const widget = container?.cesiumWidget;
  if (!widget) throw new Error("no cesiumWidget back-reference on .globe");
  const collection = widget.scene.imageryLayers;
  const layers = [];
  for (let i = 0; i < collection.length; i++) {
    const layer = collection.get(i);
    const rect = layer.imageryProvider?.rectangle;
    layers.push({
      show: layer.show,
      alpha: layer.alpha,
      widthRadians: rect ? rect.east - rect.west : null,
    });
  }
  return { layers, view: globalThis.__latentSky.getView().view };
});
console.log("imagery collection at capture time:");
for (const l of audit.layers) {
  console.log(
    `  show=${l.show} alpha=${l.alpha.toFixed(2)} lonSpan=${l.widthRadians === null ? "not ready" : l.widthRadians.toFixed(3) + " rad"}`,
  );
}
if (audit.view !== "orbit") problems.push(`expected orbit framing, got ${audit.view}`);
if (audit.layers.length !== 4) {
  problems.push(
    `expected exactly 4 imagery layers (basemap + 3 tcwv slots), found ${audit.layers.length}`,
  );
}
for (const l of audit.layers) {
  // Full globe is 2*pi ~ 6.283 rad; the hero rectangle is 0.164 rad. Any
  // hero-derived layer in the collection fails here loudly.
  if (l.widthRadians === null || l.widthRadians < 6) {
    problems.push(
      `NON-GLOBAL imagery layer present at capture time (lonSpan=${l.widthRadians}) — ` +
        "possible CC BY-NC-ND hero pixels; refusing to write og.png",
    );
  }
}

// GATE 3 (chrome) + the title treatment. Hiding the HUD grows the canvas to
// the full 630 px — give the widget's render loop a beat to resize, then
// re-render.
await page.addStyleTag({
  content: ".masthead,.corner-stack,.hud,.cesium-widget-credits{display:none !important;}",
});
await page.evaluate(() => {
  const wrap = document.querySelector(".globe-wrap");
  if (!wrap) throw new Error("no .globe-wrap");
  const title = document.createElement("div");
  title.style.cssText = "position:absolute;left:52px;top:52px;z-index:9;pointer-events:none;";
  title.innerHTML =
    '<div style="font-size:30px;font-weight:500;letter-spacing:0.3em;color:#e8eefc;' +
    'text-shadow:0 2px 16px rgba(4,8,18,0.9);">LATENT&nbsp;SKY</div>' +
    '<div style="margin-top:12px;font-size:15px;letter-spacing:0.08em;color:#a9b8d8;">' +
    "AI weather, rendered as a living globe</div>";
  const credit = document.createElement("div");
  credit.style.cssText =
    "position:absolute;left:52px;bottom:44px;z-index:9;pointer-events:none;" +
    "font-size:13px;letter-spacing:0.06em;color:#6d7fa5;";
  credit.textContent = "ERA5 reanalysis · total column water vapour";
  wrap.appendChild(title);
  wrap.appendChild(credit);
});
await page.waitForTimeout(600);
await page.evaluate(() => globalThis.__latentSky.requestRender());
await page.waitForTimeout(600);

if (problems.length === 0) {
  await page.screenshot({ path: OUT });
  // Confirm the written PNG's stated dimensions from its IHDR chunk.
  const png = await readFile(OUT);
  const w = png.readUInt32BE(16);
  const h = png.readUInt32BE(20);
  if (w !== WIDTH || h !== HEIGHT) {
    problems.push(`og.png is ${w}x${h}, expected ${WIDTH}x${HEIGHT}`);
  } else {
    console.log(`\nwrote ${OUT} — ${w}x${h}, ${png.length} bytes`);
  }
}

await browser.close();
await server.close();

if (problems.length) {
  console.error("\nOG CAPTURE FAILED:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log("all licence gates passed: no hero (CC BY-NC-ND) layer existed at capture time.");
