/**
 * Verify the REAL forecast events render — the first AI-generated run on the site.
 *
 * Unlike capture-switcher.mjs (which drives the committed two-event fixture and
 * asserts chrome geometry), this one points the app at the SHIPPED catalogue in
 * data/web and proves the things that can only be wrong once real model output
 * is involved:
 *
 *   - the catalogue carries the events the pipeline emitted
 *   - each hero event reports heroAvailable, so the reveal is offered
 *   - the manifest says run.kind === "forecast", which is what makes the UI
 *     label the fine side "AI generated" instead of "dev stand-in"
 *   - the hero framing actually paints — sampled pixels, not a screenshot, so a
 *     silently-transparent layer cannot pass
 *   - the coarse and fine sides DIFFER at the same frame, which is the entire
 *     claim the reveal makes
 *   - zero console/page errors throughout
 *
 *   node tests/verify-forecast.mjs [outDir]
 */
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT_DIR = resolve(process.argv[2] ?? join(WEB, "tests", "shots"));
const CATALOGUE = "/" + (process.env.CATALOGUE ?? "data/web/catalogue.json").replace(/^\/+/, "");
const PORT = 8627;

await mkdir(OUT_DIR, { recursive: true });

const server = await createServer({
  root: WEB,
  server: { port: PORT, strictPort: true },
  logLevel: "warn",
});
await server.listen();

const browser = await chromium.launch({
  channel: process.env.CHANNEL || "chrome",
  args: ["--enable-unsafe-swiftshader"],
});
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const problems = [];
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "assert") problems.push(m.text());
});
page.on("pageerror", (e) => problems.push(String(e)));

/** Mean RGB of the globe canvas, plus the fraction of non-background pixels. */
const canvasStats = () =>
  page.evaluate(async () => {
    globalThis.__latentSky.requestRender();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const c = document.querySelector(".globe-wrap canvas");
    const tmp = document.createElement("canvas");
    tmp.width = c.width;
    tmp.height = c.height;
    const ctx = tmp.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(c, 0, 0);
    const d = ctx.getImageData(0, 0, tmp.width, tmp.height).data;
    let r = 0, g = 0, b = 0, lit = 0, n = 0;
    for (let i = 0; i < d.length; i += 4 * 16) {
      r += d[i]; g += d[i + 1]; b += d[i + 2];
      if (d[i] + d[i + 1] + d[i + 2] > 60) lit++;
      n++;
    }
    return { r: r / n, g: g / n, b: b / n, litFraction: lit / n };
  });

async function settle(label) {
  let last = await canvasStats();
  for (let i = 0; i < 30; i++) {
    await page.waitForTimeout(300);
    const next = await canvasStats();
    if (Math.abs(next.r - last.r) < 0.01 && Math.abs(next.litFraction - last.litFraction) < 0.001) return;
    last = next;
  }
  problems.push(`${label}: canvas never settled`);
}

await page.goto(`http://localhost:${PORT}/?catalogue=${CATALOGUE}&test=1`, { waitUntil: "load" });
await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });

const events = await page.evaluate(() => globalThis.__latentSky.events);
console.log(`catalogue: ${events.length} events`);
for (const e of events) console.log(`  ${e.id.padEnd(28)} ${e.title}`);

const heroEvents = ["taiwan-gaemi-2024", "taiwan-doksuri-2023", "us-dixie-2025"];
for (const id of heroEvents) {
  if (!events.some((e) => e.id === id)) problems.push(`catalogue is missing ${id}`);
}

for (const id of heroEvents) {
  if (!events.some((e) => e.id === id)) continue;

  // Open the event DIRECTLY, exactly as a shared ?event= link does — NOT by
  // switching from the default. The distinction is load-bearing: a switch
  // inherits the previous event's base imagery, so an event tree missing its own
  // basemap still looks right. Opened cold it draws its hero patch on a black
  // globe, which is what shipped on 27 Aug 2026 and what this now catches.
  await page.goto(`http://localhost:${PORT}/?catalogue=${CATALOGUE}&event=${id}&test=1`, {
    waitUntil: "load",
  });
  await page.waitForFunction(
    (i) => globalThis.__latentSky?.ready === true && globalThis.__latentSky.activeEventId === i,
    id,
    { timeout: 60_000 },
  );

  const info = await page.evaluate(() => ({
    hero: globalThis.__latentSky.heroAvailable,
    frames: globalThis.__latentSky.frameCount,
    vars: globalThis.__latentSky.variables,
  }));
  console.log(`\n${id}: heroAvailable=${info.hero} frames=${info.frames} variables=${info.vars.join(",")}`);
  if (!info.hero) problems.push(`${id}: heroAvailable is false — the reveal will not be offered`);
  if (info.frames < 8) problems.push(`${id}: only ${info.frames} frames — too few to animate`);

  // run.kind drives the "AI generated" wording on the fine pill.
  const kind = await page.evaluate(async (i) => {
    // The ?catalogue= value is a site-absolute path, not a URL — resolve it
    // against the page before using it as a base for the manifest.
    const catUrl = new URL(new URL(location.href).searchParams.get("catalogue"), location.href);
    const cat = await (await fetch(catUrl)).json();
    const ev = cat.events.find((e) => e.id === i);
    const man = await (await fetch(new URL(ev.manifest, catUrl))).json();
    return {
      kind: man.run.kind,
      storm: man.run.stormName ?? null,
      heroFrame: man.run.heroFrame ?? null,
      basemap: man.basemap?.global ?? null,
    };
  }, id);
  console.log(
    `  manifest run.kind=${kind.kind} stormName=${kind.storm} heroFrame=${kind.heroFrame} ` +
      `basemap=${kind.basemap ?? "NONE"}`,
  );
  if (!kind.basemap) {
    problems.push(`${id}: manifest declares no basemap — opened cold this renders on a black globe`);
  }
  await settle(`${id} orbit`);
  const orbit = await canvasStats();
  console.log(`  cold-open orbit lit=${(orbit.litFraction * 100).toFixed(1)}%`);
  if (orbit.litFraction < 0.10) {
    problems.push(
      `${id}: cold-opened orbit view is ${(orbit.litFraction * 100).toFixed(1)}% lit — basemap missing`,
    );
  }
  if (kind.kind !== "forecast") {
    problems.push(`${id}: run.kind is ${kind.kind}, expected "forecast" — the UI would call it a dev stand-in`);
  }

  // Fly to the hero and prove it paints.
  await page.evaluate(() => globalThis.__latentSky.enterStorm());
  await page.waitForFunction(
    () => globalThis.__latentSky.getView().view === "hero" && globalThis.__latentSky.getView().flying === false,
    null,
    { timeout: 30_000 },
  );
  await settle(`${id} hero`);

  // Arrival fires ONE auto-sweep (7 s period, settling mid-wipe). It drives
  // sky.split from a rAF loop, so any setSplit before it finishes is overwritten
  // and the capture races the animation. Wait it out.
  await page.waitForFunction(
    () => document.querySelector(".reveal-controls .sweep")?.textContent?.trim() === "Sweep",
    null,
    { timeout: 15_000 },
  ).catch(() => problems.push(`${id}: the arrival sweep never settled`));
  await page.waitForTimeout(300);

  // The reveal's whole claim: the two sides differ. Sample the canvas with the
  // divider hard left (all fine) and hard right (all coarse) and compare.
  await page.evaluate(() => globalThis.__latentSky.setSplit(0.02));
  await page.waitForTimeout(700);
  const fine = await canvasStats();
  await page.evaluate(() => globalThis.__latentSky.setSplit(0.98));
  await page.waitForTimeout(700);
  const coarse = await canvasStats();
  const delta = Math.abs(fine.r - coarse.r) + Math.abs(fine.g - coarse.g) + Math.abs(fine.b - coarse.b);
  console.log(
    `  fine side rgb=(${fine.r.toFixed(1)},${fine.g.toFixed(1)},${fine.b.toFixed(1)}) lit=${(fine.litFraction * 100).toFixed(1)}% | ` +
      `coarse rgb=(${coarse.r.toFixed(1)},${coarse.g.toFixed(1)},${coarse.b.toFixed(1)}) | delta=${delta.toFixed(2)}`,
  );
  if (fine.litFraction < 0.05) {
    problems.push(`${id}: hero framing is essentially unlit (${(fine.litFraction * 100).toFixed(1)}%) — layer not painting`);
  }
  if (delta < 0.5) {
    problems.push(`${id}: coarse and fine sides are indistinguishable (delta=${delta.toFixed(2)}) — the reveal shows nothing`);
  }

  await page.evaluate(() => globalThis.__latentSky.setSplit(0.5));
  await page.waitForTimeout(600);
  const shot = join(OUT_DIR, `forecast-${id}.png`);
  await page.screenshot({ path: shot });
  console.log(`  wrote ${shot}`);

  await page.evaluate(() => globalThis.__latentSky.returnToOrbit());
  await page.waitForFunction(() => globalThis.__latentSky.getView().view === "orbit", null, { timeout: 30_000 });
}

await browser.close();
await server.close();

if (problems.length) {
  console.error("\nFORECAST VERIFY FAILED:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log("\nforecast verify complete — real AI output renders, reveal differs, zero errors.");
