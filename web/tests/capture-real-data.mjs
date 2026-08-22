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
// Collision review runs at 1280 (default) and 1024: CAPTURE_WIDTH=1024 node ...
const WIDTH = Number(process.env.CAPTURE_WIDTH ?? 1280);
const SUFFIX = WIDTH === 1280 ? "" : `-w${WIDTH}`;

await mkdir(SHOTS, { recursive: true });

const server = await createServer({
  root: WEB,
  server: { port: PORT, strictPort: true },
  logLevel: "warn",
});
await server.listen();

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: WIDTH, height: 800 } });
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
  const file = name.replace(/\.png$/, `${SUFFIX}.png`);
  await page.screenshot({ path: join(SHOTS, file) });
  console.log("  wrote " + file);
};

// ——— M5: the arrival (concept §8.1) — the dark planet, already turning ———
await shot("arrival-orbit.png");

// ——— The fly-down (§8.4): invitation -> ~4.5 s flight -> one auto-sweep ———
const invite = page.locator("button.invite");
if ((await invite.count()) !== 1) {
  problems.push("invitation button not found in orbit view");
}
// While the idle spin runs, the anchored invitation moves with the globe and
// Playwright refuses to click a moving target. A user's first interaction
// stops the spin (§8.1) — reproduce exactly that: one neutral pointer tap,
// then the (now stationary) invitation is genuinely clickable.
await page.mouse.move(200, 200);
await page.mouse.down();
await page.mouse.up();
await page.waitForTimeout(300);
await invite.click();
await page.waitForTimeout(2200); // mid-flight
await shot("fly-down-mid.png");
await page.waitForFunction(
  () => { const v = __latentSky.getView(); return v.view === "hero" && !v.flying; },
  null, { timeout: 20_000 },
);
// The arrival sweep: one pass each way (~5.3 s), then settles mid-wipe.
await page.waitForTimeout(6500);
const settled = await page.evaluate(() => __latentSky.getView().split);
if (settled !== 0.5) problems.push(`post-arrival sweep did not settle mid: split=${settled}`);
await shot("hero-arrival-sweep-settled.png");

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
console.log(`\n7 screenshots (${WIDTH}px wide) in ${SHOTS} — zero errors against the real dataset.`);
