/**
 * Live-site verification: loads the DEPLOYED URL in real Chrome, waits for the app
 * to declare ready, asserts the globe rendered and no errors occurred, screenshots.
 *
 *   node tests/verify-live.mjs https://d7kh11rdqrpy5.cloudfront.net/
 */
import { mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const URL = process.argv[2] ?? "https://d7kh11rdqrpy5.cloudfront.net/";
const OUT = join(dirname(dirname(fileURLToPath(import.meta.url))), "..", "data", "dev", "screenshots");
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const problems = [];
page.on("console", m => { if (m.type() === "error") problems.push("console: " + m.text()); });
page.on("pageerror", e => problems.push("page: " + e));
page.on("response", r => { if (r.status() >= 400) problems.push(`${r.status()} ${r.url()}`); });

await page.goto(URL, { waitUntil: "load" });
await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });
await page.waitForTimeout(2500);

// The globe must actually have pixels — sample for non-uniformity.
const distinct = await page.evaluate(() => {
  const c = document.querySelector("canvas");
  const t = document.createElement("canvas");
  t.width = c.width; t.height = c.height;
  t.getContext("2d").drawImage(c, 0, 0);
  const d = t.getContext("2d").getImageData(0, 0, t.width, t.height).data;
  const seen = new Set();
  for (let i = 0; i < d.length; i += 4 * 997) seen.add(`${d[i]},${d[i + 1]},${d[i + 2]}`);
  return seen.size;
});

await page.screenshot({ path: join(OUT, "LIVE-SITE.png") });
await browser.close();

console.log(`distinct sampled colours: ${distinct}`);
if (distinct < 20) problems.push(`canvas nearly uniform (${distinct} colours) — globe likely blank`);
if (problems.length) {
  console.error("LIVE VERIFICATION FAILED:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log(`LIVE SITE VERIFIED: ${URL} — screenshot at data/dev/screenshots/LIVE-SITE.png`);
