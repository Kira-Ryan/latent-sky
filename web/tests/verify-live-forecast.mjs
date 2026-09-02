/** Load the LIVE site as a visitor, open one event, prove it paints.
 *
 *   node tests/verify-live-forecast.mjs [siteUrl] [outPng]
 *   EVENT=us-dixie-2025 node tests/verify-live-forecast.mjs
 */
import { chromium } from "playwright-core";

const SITE = process.argv[2] ?? "https://latent-sky.dev";
const EVENT = process.env.EVENT ?? "taiwan-gaemi-2024";
const OUT =
  process.argv[3] ??
  `C:/Users/User/Desktop/GitHub/latent-sky/web/tests/shots/live-${EVENT}.png`;

const browser = await chromium.launch({ channel: "chrome", args: ["--enable-unsafe-swiftshader"] });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const problems = [];
page.on("console", (m) => {
  if (m.type() === "error") problems.push(m.text());
});
page.on("pageerror", (e) => problems.push(String(e)));

await page.goto(`${SITE}/?event=${EVENT}&test=1`, { waitUntil: "load", timeout: 90_000 });
await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 90_000 });

const info = await page.evaluate(() => ({
  active: globalThis.__latentSky.activeEventId,
  hero: globalThis.__latentSky.heroAvailable,
  frames: globalThis.__latentSky.frameCount,
  vars: globalThis.__latentSky.variables,
  events: globalThis.__latentSky.events.map((e) => e.id),
}));
console.log(`live: active=${info.active} hero=${info.hero} frames=${info.frames} vars=${info.vars.join(",")}`);
console.log(`events: ${info.events.join(", ")}`);
if (info.active !== EVENT) problems.push(`?event= did not select ${EVENT}: got ${info.active}`);
if (!info.hero) problems.push("heroAvailable false on the live site");

await page.evaluate(() => globalThis.__latentSky.enterStorm());
await page.waitForFunction(
  () => globalThis.__latentSky.getView().view === "hero" && globalThis.__latentSky.getView().flying === false,
  null,
  { timeout: 60_000 },
);
await page
  .waitForFunction(
    () => document.querySelector(".reveal-controls .sweep")?.textContent?.trim() === "Sweep",
    null,
    { timeout: 20_000 },
  )
  .catch(() => problems.push("arrival sweep never settled"));
await page.waitForTimeout(1000);

// The pills state the resolution to the viewer — assert they read from the
// manifest rather than a hardcoded literal.
const pills = await page.evaluate(() => ({
  coarse: document.querySelector(".pill.coarse")?.textContent?.trim() ?? null,
  fine: document.querySelector(".pill.fine")?.textContent?.trim() ?? null,
}));
console.log(`pills: coarse="${pills.coarse}" | fine="${pills.fine}"`);

const lit = await page.evaluate(async () => {
  globalThis.__latentSky.requestRender();
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  const c = document.querySelector(".globe-wrap canvas");
  const t = document.createElement("canvas");
  t.width = c.width;
  t.height = c.height;
  const ctx = t.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(c, 0, 0);
  const d = ctx.getImageData(0, 0, t.width, t.height).data;
  let on = 0,
    n = 0;
  for (let i = 0; i < d.length; i += 64) {
    if (d[i] + d[i + 1] + d[i + 2] > 60) on++;
    n++;
  }
  return on / n;
});
console.log(`hero framing lit fraction: ${(lit * 100).toFixed(1)}%`);
if (lit < 0.05) problems.push("live hero framing is essentially unlit");

await page.screenshot({ path: OUT });
console.log(`wrote ${OUT}`);
await browser.close();

if (problems.length) {
  console.error("LIVE CHECK FAILED:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log(`live check passed — ${EVENT} renders on the deployed site.`);
