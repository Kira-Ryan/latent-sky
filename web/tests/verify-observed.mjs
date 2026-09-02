/**
 * Verify a forecast-vs-observation reveal: an event whose reflectivity layer is
 * paired with an observed (MRMS) layer rather than a coarse model input.
 *
 * Proves, in a real browser against the served catalogue:
 *   - the event cold-opens and the hero framing paints
 *   - switching to reflectivity yields a pair whose LEFT pill says "MRMS observed"
 *     with the observed layer's own resolution, not "model input"
 *   - the two sides of the wipe measurably differ (forecast is not observation)
 *   - the scrubber's aria text follows the pair kind
 *   - zero console/page errors
 *
 *   node tests/verify-observed.mjs [eventId] [outDir]
 */
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const EVENT = process.argv[2] ?? "us-dixie-2025";
const OUT_DIR = resolve(process.argv[3] ?? join(WEB, "tests", "shots"));
const CATALOGUE = "/" + (process.env.CATALOGUE ?? "data/web/catalogue.json").replace(/^\/+/, "");
const PORT = 8629;

await mkdir(OUT_DIR, { recursive: true });
const server = await createServer({ root: WEB, server: { port: PORT, strictPort: true }, logLevel: "warn" });
await server.listen();

const browser = await chromium.launch({ channel: process.env.CHANNEL || "chrome", args: ["--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const problems = [];
page.on("console", (m) => { if (m.type() === "error" || m.type() === "assert") problems.push(m.text()); });
page.on("pageerror", (e) => problems.push(String(e)));

const stats = () =>
  page.evaluate(async () => {
    globalThis.__latentSky.requestRender();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const c = document.querySelector(".globe-wrap canvas");
    const t = document.createElement("canvas");
    t.width = c.width; t.height = c.height;
    const ctx = t.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(c, 0, 0);
    const d = ctx.getImageData(0, 0, t.width, t.height).data;
    let r = 0, g = 0, b = 0, n = 0;
    for (let i = 0; i < d.length; i += 64) { r += d[i]; g += d[i + 1]; b += d[i + 2]; n++; }
    return { r: r / n, g: g / n, b: b / n };
  });

await page.goto(`http://localhost:${PORT}/?catalogue=${CATALOGUE}&event=${EVENT}&test=1`, { waitUntil: "load" });
await page.waitForFunction((id) => globalThis.__latentSky?.ready === true && globalThis.__latentSky.activeEventId === id, EVENT, { timeout: 60_000 });

await page.evaluate(() => globalThis.__latentSky.enterStorm());
await page.waitForFunction(() => globalThis.__latentSky.getView().view === "hero" && !globalThis.__latentSky.getView().flying, null, { timeout: 30_000 });
await page.waitForFunction(() => document.querySelector(".reveal-controls .sweep")?.textContent?.trim() === "Sweep", null, { timeout: 15_000 })
  .catch(() => problems.push("arrival sweep never settled"));

// The reflectivity pair is the one paired with observation.
await page.evaluate(() => globalThis.__latentSky.setVariable("refc"));
await page.evaluate(() => globalThis.__latentSky.setFrame(10)); // 04Z, the peak hour
await page.waitForTimeout(1500);

const pills = await page.evaluate(() => ({
  left: document.querySelector(".pill.coarse")?.textContent?.trim() ?? null,
  right: document.querySelector(".pill.fine")?.textContent?.trim() ?? null,
  aria: document.querySelector(".reveal-controls input[type=range]")?.getAttribute("aria-valuetext") ?? null,
}));
console.log(`pills: left="${pills.left}" | right="${pills.right}" | aria="${pills.aria}"`);
if (!pills.left?.includes("MRMS observed")) problems.push(`left pill should say MRMS observed, got "${pills.left}"`);
if (!pills.left?.startsWith("≈ 1 km")) problems.push(`left pill should carry MRMS's ~1 km resolution, got "${pills.left}"`);
if (!pills.right?.includes("3 km")) problems.push(`right pill should carry StormCast's 3 km, got "${pills.right}"`);
if (!pills.aria?.includes("observed")) problems.push(`aria text should say observed, got "${pills.aria}"`);

await page.evaluate(() => globalThis.__latentSky.setSplit(0.98)); await page.waitForTimeout(700);
const observed = await stats();
await page.evaluate(() => globalThis.__latentSky.setSplit(0.02)); await page.waitForTimeout(700);
const forecast = await stats();
const delta = Math.abs(observed.r - forecast.r) + Math.abs(observed.g - forecast.g) + Math.abs(observed.b - forecast.b);
console.log(`observed side rgb=(${observed.r.toFixed(1)},${observed.g.toFixed(1)},${observed.b.toFixed(1)}) | forecast side rgb=(${forecast.r.toFixed(1)},${forecast.g.toFixed(1)},${forecast.b.toFixed(1)}) | delta=${delta.toFixed(2)}`);
if (delta < 0.5) problems.push(`observed and forecast sides are indistinguishable (delta=${delta.toFixed(2)})`);

await page.evaluate(() => globalThis.__latentSky.setSplit(0.5)); await page.waitForTimeout(600);
const shot = join(OUT_DIR, `observed-${EVENT}.png`);
await page.screenshot({ path: shot });
console.log(`wrote ${shot}`);

await browser.close(); await server.close();
if (problems.length) { console.error("\nOBSERVED VERIFY FAILED:"); for (const p of problems) console.error("  " + p); process.exit(1); }
console.log("observed verify complete — forecast vs MRMS reveal renders, pills correct, zero errors.");
