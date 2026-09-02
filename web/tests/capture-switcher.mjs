/**
 * Event-switcher capture — visual QA for the masthead control and for what an
 * event switch actually leaves on screen.
 *
 * Serves the app with the Vite dev server against the committed two-event
 * fixture catalogue (or any catalogue via CATALOGUE=) and writes 1280x800
 * screenshots of the three states worth eyeballing:
 *
 *   switcher-hero.png    the default (hero-bearing) event, control collapsed
 *   switcher-open.png    the listbox open over that event — the collision test
 *                        against the variable pills and the legend
 *   switcher-global.png  after switching to the global-only event: no hero
 *                        chrome, pre-forecast strip, control naming the new event
 *
 * Asserts, because a screenshot cannot:
 *   - the collapsed control does not overlap the top-right corner stack
 *   - the open listbox does not overlap the corner stack or the HUD
 *   - the switch actually landed (?event=, run id, hero chrome)
 *   - zero console/page errors throughout
 *
 * Exits non-zero on any failure.
 *
 *   node tests/capture-switcher.mjs [outDir]        (default: tests/shots)
 *   CHANNEL=chromium node tests/capture-switcher.mjs
 */
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT_DIR = resolve(process.argv[2] ?? join(WEB, "tests", "shots"));
// Give CATALOGUE without a leading slash: Git Bash path-converts env values
// that start with "/" into C:/Program Files/…
const CATALOGUE = "/" + (process.env.CATALOGUE ?? "dev-fixture/catalogue.json").replace(/^\/+/, "");
const PORT = 8623;
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

await page.goto(`http://localhost:${PORT}/?catalogue=${CATALOGUE}&test=1`, { waitUntil: "load" });
await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });

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
    if (last.length === next.length && last.every((p, i) => p.join(",") === next[i].join(","))) return;
    last = next;
  }
  problems.push(`${label}: canvas never settled — capture would race the decode`);
}

/** Bounding boxes of the pieces of chrome that must not overlap each other. */
const boxes = () =>
  page.evaluate(() => {
    const rect = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.x, y: r.y, w: r.width, h: r.height, right: r.right, bottom: r.bottom };
    };
    return {
      trigger: rect(".switcher .trigger"),
      list: rect(".switcher .list"),
      corner: rect(".corner-stack"),
      masthead: rect(".masthead"),
      hud: rect(".hud"),
      invite: rect(".invite"),
      coming: rect(".coming"),
    };
  });

function overlaps(a, b) {
  if (!a || !b) return false;
  return a.x < b.right && b.x < a.right && a.y < b.bottom && b.y < a.bottom;
}

// Stop the idle spin deterministically — a neutral tap over empty sky, well
// clear of the masthead, so the framing is identical shot to shot.
await page.mouse.move(640, 700);
await page.mouse.down();
await page.mouse.up();
await page.waitForTimeout(300);

// ——— 1. the hero-bearing default event, control collapsed ———
await settle("hero event");
const heroBoxes = await boxes();
if (!heroBoxes.trigger) problems.push("no .switcher .trigger — the switcher did not render");
if (overlaps(heroBoxes.trigger, heroBoxes.corner)) {
  problems.push(
    `collapsed switcher overlaps the corner stack: trigger right=${heroBoxes.trigger?.right.toFixed(0)} ` +
      `vs corner left=${heroBoxes.corner?.x.toFixed(0)}`,
  );
}
console.log(
  `collapsed trigger: x=${heroBoxes.trigger?.x.toFixed(0)} right=${heroBoxes.trigger?.right.toFixed(0)} ` +
    `bottom=${heroBoxes.trigger?.bottom.toFixed(0)} | corner stack left=${heroBoxes.corner?.x.toFixed(0)}`,
);
const heroShot = join(OUT_DIR, "switcher-hero.png");
await page.screenshot({ path: heroShot });
console.log(`wrote ${heroShot}`);

// ——— 1b. the hero framing, where "Return to orbit" docks under the masthead.
// The switcher sits on the wordmark's line, so the masthead is TALLER than it
// was; this is the one place that extra height can collide with something. ———
await page.evaluate(() => globalThis.__latentSky.enterStorm());
await page.waitForFunction(
  () => globalThis.__latentSky.getView().view === "hero" && globalThis.__latentSky.getView().flying === false,
  null,
  { timeout: 30_000 },
);
await settle("hero framing");
const heroView = await page.evaluate(() => {
  const rect = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { y: r.y, bottom: r.bottom };
  };
  return { masthead: rect(".masthead"), returnBtn: rect(".return") };
});
if (!heroView.returnBtn) {
  problems.push('no "Return to orbit" button at the hero framing');
} else {
  const clearance = heroView.returnBtn.y - (heroView.masthead?.bottom ?? 0);
  console.log(
    `hero framing: masthead bottom=${heroView.masthead?.bottom.toFixed(0)} ` +
      `return top=${heroView.returnBtn.y.toFixed(0)} clearance=${clearance.toFixed(0)}px`,
  );
  if (clearance < 6) {
    problems.push(
      `"Return to orbit" clears the masthead by only ${clearance.toFixed(0)}px — the switcher ` +
        "made the masthead taller; raise .return's top offset",
    );
  }
}
const heroViewShot = join(OUT_DIR, "switcher-hero-view.png");
await page.screenshot({ path: heroViewShot });
console.log(`wrote ${heroViewShot}`);
await page.evaluate(() => globalThis.__latentSky.returnToOrbit());
await page.waitForFunction(() => globalThis.__latentSky.getView().view === "orbit", null, {
  timeout: 30_000,
});
await settle("back in orbit");

// ——— 2. the listbox open ———
await page.focus(".switcher .trigger");
await page.keyboard.press("Enter");
await page.waitForSelector("[role=listbox] [role=option]", { timeout: 5_000 });
await page.waitForTimeout(350); // the chevron rotation settles
const openBoxes = await boxes();
if (!openBoxes.list) problems.push("the listbox did not render on Enter");
if (overlaps(openBoxes.list, openBoxes.corner)) {
  problems.push(
    `open listbox overlaps the corner stack (variable pills / legend): list right=` +
      `${openBoxes.list?.right.toFixed(0)} vs corner left=${openBoxes.corner?.x.toFixed(0)}`,
  );
}
if (overlaps(openBoxes.list, openBoxes.hud)) problems.push("open listbox overlaps the HUD");
if (overlaps(openBoxes.list, openBoxes.invite)) problems.push("open listbox overlaps the invitation");
console.log(
  `open listbox: x=${openBoxes.list?.x.toFixed(0)} right=${openBoxes.list?.right.toFixed(0)} ` +
    `bottom=${openBoxes.list?.bottom.toFixed(0)} | corner left=${openBoxes.corner?.x.toFixed(0)} ` +
    `| hud top=${openBoxes.hud?.y.toFixed(0)}`,
);
const openShot = join(OUT_DIR, "switcher-open.png");
await page.screenshot({ path: openShot });
console.log(`wrote ${openShot}`);

// ——— 3. switch to the second event ———
const targetId = await page.evaluate(() => globalThis.__latentSky.events[1].id);
await page.keyboard.press("ArrowDown");
await page.keyboard.press("Enter");
await page.waitForFunction(
  (id) => globalThis.__latentSky.ready === true && globalThis.__latentSky.activeEventId === id,
  targetId,
  { timeout: 60_000 },
);
await settle("global event");
const after = await page.evaluate(() => ({
  event: new URL(location.href).searchParams.get("event"),
  runId: document.querySelector(".runid")?.textContent?.trim() ?? null,
  trigger: document.querySelector(".switcher .trigger-title")?.textContent?.trim() ?? null,
  hero: globalThis.__latentSky.heroAvailable,
  invite: document.querySelectorAll(".invite").length,
  coming: document.querySelectorAll(".coming").length,
}));
if (after.event !== targetId) problems.push(`?event= is ${after.event}, expected ${targetId}`);
if (after.hero !== false || after.invite !== 0 || after.coming !== 1) {
  problems.push(
    `hero chrome did not stand down after the switch: hero=${after.hero} invite=${after.invite} coming=${after.coming}`,
  );
}
const globalBoxes = await boxes();
if (overlaps(globalBoxes.trigger, globalBoxes.corner)) {
  problems.push("collapsed switcher overlaps the corner stack on the global event");
}
if (overlaps(globalBoxes.trigger, globalBoxes.coming)) {
  problems.push("collapsed switcher overlaps the pre-forecast strip");
}
console.log(`switched to ${after.event} — run ${after.runId}, switcher reads "${after.trigger}"`);
const globalShot = join(OUT_DIR, "switcher-global.png");
await page.screenshot({ path: globalShot });
console.log(`wrote ${globalShot}`);

// ——— narrower viewports: the masthead is wider with the switcher in it, so
// the top-right stack's reserve has to grow with it (App.svelte
// --masthead-reserve). Measured, not eyeballed, at the widths the project
// already calls out as the crowding cases. ———
for (const width of [1024, 900, 820]) {
  await page.setViewportSize({ width, height: HEIGHT });
  await page.waitForTimeout(250);
  const b = await boxes();
  const gap = (b.corner?.x ?? 0) - (b.trigger?.right ?? 0);
  console.log(
    `@${width}px: trigger right=${b.trigger?.right.toFixed(0)} corner left=${b.corner?.x.toFixed(0)} gap=${gap.toFixed(0)}px`,
  );
  if (overlaps(b.trigger, b.corner)) {
    problems.push(`@${width}px: the switcher overlaps the variable pills / legend stack`);
  }
  await page.focus(".switcher .trigger");
  await page.keyboard.press("Enter");
  await page.waitForSelector("[role=listbox] [role=option]", { timeout: 5_000 });
  const bo = await boxes();
  if (overlaps(bo.list, bo.corner)) problems.push(`@${width}px: the open listbox overlaps the corner stack`);
  if (overlaps(bo.list, bo.hud)) problems.push(`@${width}px: the open listbox overlaps the HUD`);
  await page.keyboard.press("Escape");
}
await page.setViewportSize({ width: WIDTH, height: HEIGHT });

await browser.close();
await server.close();

if (problems.length) {
  console.error("\nSWITCHER CAPTURE FAILED:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log("switcher capture complete — zero console/page errors, zero chrome collisions.");
