/**
 * Hero-free experience capture — visual QA for the public pre-forecast state
 * (global ERA5-class layers only, no hero pair, no invitation, no reveal).
 *
 * Serves the app with the Vite dev server against the committed hero-free
 * synthetic fixture (or any hero-free manifest via MANIFEST=) and writes
 * 1280x800 screenshots of the three stable states:
 *
 *   <tag>-arrival.png     wind10m, frame 0 — the arrival framing
 *   <tag>-tcwv.png        tcwv, last frame — after variable switch + scrub
 *   <tag>-zoom-floor.png  wind10m, wheel-zoomed to the hero-free camera floor
 *
 * Also asserts, because the arrival is where the piece lives:
 *   - the load framing is the western Pacific (lat ≈ 18°, lon ≈ 125°)
 *   - the idle spin changes longitude ONLY — latitude must hold, or the
 *     visitor drifts towards an ambiguous pole-ish view before ever touching
 *     the globe
 *   - wheel zoom stops at the 8,000 km presentation floor (flight.ts)
 *
 * Exits non-zero on any console/page error or failed assertion. Intended for
 * eyeballing dead space or dangling UI on top of that — pixel assertions live
 * in the smoke test.
 *
 *   node tests/capture-hero-free.mjs [outDir]        (default: tests/shots)
 *   CHANNEL=chromium node tests/capture-hero-free.mjs
 *   MANIFEST=data/web/manifest.json TAG=public node tests/capture-hero-free.mjs
 */
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT_DIR = resolve(process.argv[2] ?? join(WEB, "tests", "shots"));
// Give MANIFEST without a leading slash (e.g. MANIFEST=data/web/manifest.json):
// Git Bash path-converts env values that start with "/" into C:/Program Files/…
const MANIFEST = "/" + (process.env.MANIFEST ?? "dev-fixture/manifest-hero-free.json").replace(/^\/+/, "");
const TAG = process.env.TAG ?? "hero-free";
const PORT = 8523;
const WIDTH = 1280;
const HEIGHT = 800;
/** flight.ts HERO_FREE_MIN_ZOOM — the camera floor under a hero-free manifest. */
const FLOOR_M = 8_000_000;

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
await page.goto(`http://localhost:${PORT}/?manifest=${MANIFEST}&test=1`, {
  waitUntil: "load",
});
await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, {
  timeout: 60_000,
});

/** Camera cartographic position via the widget debug back-reference. */
const cameraPos = () =>
  page.evaluate(() => {
    const widget = document.querySelector(".globe")?.cesiumWidget;
    if (!widget) throw new Error("no widget back-reference on .globe");
    const c = widget.camera.positionCartographic;
    return { lat: (c.latitude * 180) / Math.PI, lon: (c.longitude * 180) / Math.PI, height: c.height };
  });

// The load framing must be the western Pacific (flight.ts orbitDestination:
// 125°E, 18°N), and the idle spin must change longitude ONLY — a latitude
// drift is exactly the "ambiguous pole-ish view" failure being guarded.
const spinA = await cameraPos();
await page.waitForTimeout(1500);
const spinB = await cameraPos();
if (Math.abs(spinA.lat - 18) > 0.05) {
  problems.push(`load framing latitude ${spinA.lat.toFixed(3)}° — expected 18°`);
}
if (Math.abs(spinB.lat - spinA.lat) > 0.01) {
  problems.push(`idle spin drifted latitude ${spinA.lat.toFixed(4)}° -> ${spinB.lat.toFixed(4)}°`);
}
if (Math.abs(spinB.lon - spinA.lon) < 0.1) {
  problems.push(
    `idle spin did not advance longitude (${spinA.lon.toFixed(3)}° -> ${spinB.lon.toFixed(3)}°) — ` +
      "arrival motion is missing (ignore if prefers-reduced-motion is forced)",
  );
}
console.log(
  `arrival framing: lat ${spinB.lat.toFixed(2)}°, lon ${spinA.lon.toFixed(2)}° -> ${spinB.lon.toFixed(2)}° (spin), height ${(spinB.height / 1000).toFixed(0)} km`,
);

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
const arrival = join(OUT_DIR, `${TAG}-arrival.png`);
await page.screenshot({ path: arrival });
console.log(`wrote ${arrival}`);

await page.evaluate(() => {
  globalThis.__latentSky.setVariable("tcwv");
  globalThis.__latentSky.setFrame(globalThis.__latentSky.frameCount - 1);
});
await settle("tcwv");
const tcwv = join(OUT_DIR, `${TAG}-tcwv.png`);
await page.screenshot({ path: tcwv });
console.log(`wrote ${tcwv}`);

// ——— the zoom floor: wheel down until the controller refuses ———
// Back to the default variable and frame, then wheel-zoom at the globe centre
// exactly as a visitor would. The ScreenSpaceCameraController clamps against
// the camera's height, so this proves the floor a gesture actually hits.
await page.evaluate(() => {
  globalThis.__latentSky.setVariable("wind10m");
  globalThis.__latentSky.setFrame(0);
});
await settle("pre-zoom");
await page.mouse.move(WIDTH / 2, HEIGHT / 2 - 40); // over the globe, above the HUD
let height = (await cameraPos()).height;
for (let i = 0; i < 60; i++) {
  await page.mouse.wheel(0, -600);
  await page.waitForTimeout(120);
  const next = (await cameraPos()).height;
  if (Math.abs(next - height) < 500 && i > 3) {
    height = next;
    break; // plateaued — the floor
  }
  height = next;
}
console.log(`wheel zoom stopped at ${(height / 1000).toFixed(0)} km altitude`);
if (height < FLOOR_M * 0.97) {
  problems.push(`zoom descended to ${(height / 1000).toFixed(0)} km — below the ${FLOOR_M / 1000} km floor`);
}
if (height > FLOOR_M * 1.35) {
  problems.push(
    `zoom stopped at ${(height / 1000).toFixed(0)} km — never reached the floor, so the clamp is unproven`,
  );
}
await settle("zoom-floor");
const zoomed = join(OUT_DIR, `${TAG}-zoom-floor.png`);
await page.screenshot({ path: zoomed });
console.log(`wrote ${zoomed}`);

await browser.close();
await server.close();

if (problems.length) {
  console.error("\nHERO-FREE CAPTURE FAILED:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log("hero-free capture complete — zero console/page errors.");
