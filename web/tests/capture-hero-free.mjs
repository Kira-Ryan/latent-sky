/**
 * Hero-free experience capture — visual QA for the public pre-forecast state
 * (global ERA5-class layers only, no hero pair, no invitation, no reveal).
 *
 * Serves the app with the Vite dev server against the committed hero-free
 * synthetic fixture and writes 1280x800 screenshots of the two stable states:
 *
 *   hero-free-arrival.png   wind10m, frame 0 — the arrival framing
 *   hero-free-tcwv.png      tcwv, last frame — after variable switch + scrub
 *
 * Exits non-zero on any console/page error. Intended for eyeballing dead
 * space or dangling UI, not for pixel assertions — those live in the smoke
 * test.
 *
 *   node tests/capture-hero-free.mjs [outDir]        (default: tests/shots)
 *   CHANNEL=chromium node tests/capture-hero-free.mjs
 */
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT_DIR = resolve(process.argv[2] ?? join(WEB, "tests", "shots"));
const PORT = 8523;
const WIDTH = 1280;
const HEIGHT = 800;

await mkdir(OUT_DIR, { recursive: true });

const server = await createServer({
  root: WEB,
  server: { port: PORT, strictPort: true },
  logLevel: "warn",
});
await server.listen();

const CHANNEL = process.env.CHANNEL || "chrome";
const browser = await chromium.launch({
  channel: CHANNEL,
  args: ["--enable-unsafe-swiftshader"],
});
const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT } });
const problems = [];
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "assert") problems.push(m.text());
});
page.on("pageerror", (e) => problems.push(String(e)));

// ?test=1 turns on preserveDrawingBuffer so the settle poll can read pixels.
await page.goto(`http://localhost:${PORT}/?manifest=/dev-fixture/manifest-hero-free.json&test=1`, {
  waitUntil: "load",
});
await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, {
  timeout: 60_000,
});

// Stop the idle spin deterministically — same neutral tap as capture-og.
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

/** Wait until two consecutive samples are identical (decode settled). */
async function settle(label) {
  let last = await sample();
  for (let attempt = 0; attempt < 30; attempt++) {
    await page.waitForTimeout(300);
    const next = await sample();
    if (last.every((p, i) => p.join(",") === next[i].join(","))) return;
    last = next;
  }
  problems.push(`${label}: canvas never settled — capture would race the decode`);
}

await settle("arrival");
const arrival = join(OUT_DIR, "hero-free-arrival.png");
await page.screenshot({ path: arrival });
console.log(`wrote ${arrival}`);

await page.evaluate(() => {
  globalThis.__latentSky.setVariable("tcwv");
  globalThis.__latentSky.setFrame(globalThis.__latentSky.frameCount - 1);
});
await settle("tcwv");
const tcwv = join(OUT_DIR, "hero-free-tcwv.png");
await page.screenshot({ path: tcwv });
console.log(`wrote ${tcwv}`);

await browser.close();
await server.close();

if (problems.length) {
  console.error("\nHERO-FREE CAPTURE FAILED:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log("hero-free capture complete — zero console/page errors.");
