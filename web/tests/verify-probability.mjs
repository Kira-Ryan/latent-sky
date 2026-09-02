/**
 * Verify the ensemble probability layer: a hero-fine variable with no coarse
 * counterpart, paired with the observed (MRMS) radar for the reveal.
 *
 * Proves, in a real browser against the served catalogue:
 *   - the picker offers "Probability" and selecting it lands on prob40-fine
 *   - the legend reads the ramp's own label and units (percent)
 *   - the LEFT pill is the observed radar, the RIGHT pill is the 3 km AI layer
 *   - the two sides of the wipe measurably differ
 *   - zero console/page errors
 *
 *   node tests/verify-probability.mjs [eventId] [outDir]
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
const PORT = 8631;

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

const pickerNames = await page.evaluate(() => [...document.querySelectorAll('[role="radiogroup"] [role="radio"]')].map((b) => b.textContent.trim()));
console.log(`picker: ${pickerNames.join(" | ")}`);
if (!pickerNames.includes("Probability")) problems.push(`picker should offer Probability, got ${pickerNames.join(", ")}`);

await page.evaluate(() => globalThis.__latentSky.enterStorm());
await page.waitForFunction(() => globalThis.__latentSky.getView().view === "hero" && !globalThis.__latentSky.getView().flying, null, { timeout: 30_000 });
await page.waitForFunction(() => document.querySelector(".reveal-controls .sweep")?.textContent?.trim() === "Sweep", null, { timeout: 15_000 })
  .catch(() => problems.push("arrival sweep never settled"));

await page.evaluate(() => globalThis.__latentSky.setVariable("prob40"));
await page.evaluate(() => globalThis.__latentSky.setFrame(10)); // 04Z, the peak hour
await page.waitForTimeout(1500);

const ui = await page.evaluate(() => ({
  left: document.querySelector(".pill.coarse")?.textContent?.trim() ?? null,
  right: document.querySelector(".pill.fine")?.textContent?.trim() ?? null,
  legend: document.querySelector("figcaption.title")?.textContent?.trim() ?? null,
  active: [...document.querySelectorAll('[role="radio"][aria-checked="true"]')].map((b) => b.textContent.trim()).join(","),
}));
console.log(`legend="${ui.legend}" | active="${ui.active}" | left="${ui.left}" | right="${ui.right}"`);
if (ui.active !== "Probability") problems.push(`Probability should be the checked radio, got "${ui.active}"`);
if (!ui.legend?.includes("Probability of") || !ui.legend?.includes("%")) problems.push(`legend should carry the ramp label and percent, got "${ui.legend}"`);
if (!ui.left?.includes("MRMS observed")) problems.push(`left pill should be the observed radar, got "${ui.left}"`);
if (!ui.right?.includes("3 km") || !ui.right?.includes("AI generated")) problems.push(`right pill should be the 3 km AI layer, got "${ui.right}"`);

await page.evaluate(() => globalThis.__latentSky.setSplit(0.98)); await page.waitForTimeout(700);
const observed = await stats();
await page.evaluate(() => globalThis.__latentSky.setSplit(0.02)); await page.waitForTimeout(700);
const probability = await stats();
const delta = Math.abs(observed.r - probability.r) + Math.abs(observed.g - probability.g) + Math.abs(observed.b - probability.b);
console.log(`observed side rgb=(${observed.r.toFixed(1)},${observed.g.toFixed(1)},${observed.b.toFixed(1)}) | probability side rgb=(${probability.r.toFixed(1)},${probability.g.toFixed(1)},${probability.b.toFixed(1)}) | delta=${delta.toFixed(2)}`);
if (delta < 0.5) problems.push(`observed and probability sides are indistinguishable (delta=${delta.toFixed(2)})`);

await page.evaluate(() => globalThis.__latentSky.setSplit(0.5)); await page.waitForTimeout(600);
const shot = join(OUT_DIR, `probability-${EVENT}.png`);
await page.screenshot({ path: shot });
console.log(`wrote ${shot}`);

await browser.close(); await server.close();
if (problems.length) { console.error("\nPROBABILITY VERIFY FAILED:"); for (const p of problems) console.error("  " + p); process.exit(1); }
console.log("probability verify complete — ensemble layer renders against the radar, labels correct, zero errors.");
