/**
 * Integration check against the REAL dev dataset (Typhoon Chanthu, CC BY-NC-ND — local only).
 *
 * Starts Vite programmatically, loads the app with ?manifest=/data/dev/encoded/manifest.json,
 * scrubs to the typhoon frame, exercises the reveal wipe, and writes screenshots to
 * ../../data/dev/screenshots/ (gitignored). Exits non-zero on any console/page error.
 *
 *   PATH="/c/Users/User/AppData/Roaming/nvm/v22.21.1:$PATH" node tests/capture-real-data.mjs
 */
import { mkdir } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const SHOTS = join(WEB, "..", "data", "dev", "screenshots");
const PORT = 8422;

await mkdir(SHOTS, { recursive: true });

const server = await createServer({
  root: WEB,
  server: { port: PORT, strictPort: true },
  logLevel: "warn",
});
await server.listen();

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const problems = [];
page.on("console", m => { if (m.type() === "error") problems.push(m.text()); });
page.on("pageerror", e => problems.push(String(e)));

await page.goto(
  `http://localhost:${PORT}/?manifest=/data/dev/encoded/manifest.json`,
  { waitUntil: "load" },
);
await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });
await page.waitForTimeout(1500);

const shot = async (name) => {
  await page.waitForTimeout(400);
  await page.screenshot({ path: join(SHOTS, name) });
  console.log("  wrote " + name);
};

// Frame 3 = 2021-09-12 00Z, Typhoon Chanthu.
await page.evaluate(() => __latentSky.setFrame(3));
await page.waitForTimeout(1200);

await page.evaluate(() => __latentSky.setSplit(1.0));   // coarse only
await shot("chanthu-coarse.png");
await page.evaluate(() => __latentSky.setSplit(0.0));   // fine only
await shot("chanthu-fine.png");
await page.evaluate(() => __latentSky.setSplit(0.5));   // the reveal
await shot("chanthu-reveal.png");

await page.evaluate(() => { __latentSky.setFrame(0); __latentSky.setSplit(0.0); });
await page.waitForTimeout(1200);
await shot("quiet-feb.png");

await browser.close();
await server.close();

if (problems.length) {
  console.error("ERRORS against real data:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log(`\n4 screenshots in ${SHOTS} — zero errors against the real dataset.`);
