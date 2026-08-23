/**
 * Smoke test — probe3 style: plain node script driving REAL Chrome via
 * playwright-core (channel "chrome"), no playwright test-runner.
 *
 * Serves the app with the Vite dev server and runs the suite against BOTH
 * committed synthetic fixtures (web/dev-fixture/):
 *
 *   manifest.json            with-hero — hero pairs + global layers + basemap
 *   manifest-hero-free.json  hero-free — global layers only, the public
 *                            pre-forecast state the site first deploys in
 *
 * Shared assertions, per fixture:
 *   1. the canvas is not a uniform colour (the globe and field rendered)
 *   2. scrubbing one frame forward changes pixels
 *   3. tcwv (a global-only layer) is selectable and renders differently
 *   4. the manifest basemap is used — zero NaturalEarthII fallback requests
 *   5. zero console errors / failed console.asserts / page errors
 *   6. zero responses >= 400
 *   7. zero requests to api.cesium.com or ANY non-localhost host
 *
 * With-hero only:
 *   8. the invitation ("enter the storm") is present in orbit
 *   9. the pre-forecast strip is absent — a shipped hero IS the way down
 *
 * Hero-free only:
 *   8. no invitation, no hero marker, no place label, no reveal overlay,
 *      no "Generated detail" toggle — no dead or dangling hero UI
 *   9. the quiet pre-forecast strip is present with the built-in copy
 *  10. __latentSky.enterStorm() is a safe no-op (still orbit, nothing thrown)
 *
 * Exits non-zero on any failure.
 *
 *   node tests/smoke.spec.mjs
 *
 * Browser channel: real Chrome locally, overridable for CI where only
 * Playwright's bundled Chromium exists (installed via `npx playwright-core
 * install chromium`):
 *
 *   CHANNEL=chromium node tests/smoke.spec.mjs
 */
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // web/
const PORT = 8323;

const FIXTURES = [
  { name: "with-hero", manifest: "/dev-fixture/manifest.json", hero: true },
  { name: "hero-free", manifest: "/dev-fixture/manifest-hero-free.json", hero: false },
];

for (const fixture of FIXTURES) {
  const file = join(ROOT, ...fixture.manifest.split("/").filter(Boolean));
  if (!existsSync(file)) {
    console.error(`fixture missing: ${file} — run \`npm run fixture\` first`);
    process.exit(1);
  }
}

const failures = [];

const server = await createServer({
  root: ROOT,
  server: { port: PORT, strictPort: true },
  logLevel: "warn",
});
await server.listen();

const CHANNEL = process.env.CHANNEL || "chrome"; // "chromium" in CI (web.yml)
console.log(`browser channel: ${CHANNEL}`);
const browser = await chromium.launch({
  channel: CHANNEL,
  args: ["--enable-unsafe-swiftshader"], // deterministic software GL if no hw context
});

/** Run the full suite against one fixture on a fresh page. */
async function runFixture(fixture) {
  console.log(`\n=== fixture: ${fixture.name} (${fixture.manifest}) ===`);
  const check = (ok, label, detail = "") => {
    console.log(`${ok ? "  PASS" : "  FAIL"}  ${label}${detail ? ` — ${detail}` : ""}`);
    if (!ok) failures.push(`[${fixture.name}] ${label}`);
  };

  const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
  const consoleErrors = [];
  const pageErrors = [];
  const badResponses = [];
  const externalRequests = [];
  const allRequests = [];
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "assert") consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => pageErrors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400) badResponses.push(`${r.status()} ${r.url()}`);
  });
  page.on("request", (r) => {
    allRequests.push(r.url());
    const host = new URL(r.url()).hostname;
    if (!["localhost", "127.0.0.1", "[::1]"].includes(host)) externalRequests.push(r.url());
  });

  try {
    await page.goto(`http://localhost:${PORT}/?manifest=${fixture.manifest}&test=1`, {
      waitUntil: "load",
    });
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, {
      timeout: 45_000,
    });
    console.log("app reported ready (manifest loaded, layers built, tiles loaded)\n");

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

    const frame0 = await sample();
    const distinct = new Set(frame0.map((p) => p.join(","))).size;
    check(distinct >= 10, "canvas is not a uniform colour", `${distinct} distinct colours across ${frame0.length} samples`);

    await page.evaluate(() => globalThis.__latentSky.setFrame(1));
    const frame1 = await sample();
    const changed = frame0.filter((p, i) => p.join(",") !== frame1[i].join(",")).length;
    check(changed > 5, "scrubbing one frame forward changes pixels", `${changed}/${frame0.length} sample points changed`);

    // Global-only variable: tcwv has no hero layers, so its being selectable and
    // rendering proves the kind:"global" path end to end.
    const variables = await page.evaluate(() => globalThis.__latentSky.variables);
    check(
      Array.isArray(variables) && variables.includes("tcwv"),
      "tcwv (global-only) is offered as a selectable variable",
      `variables = ${JSON.stringify(variables)}`,
    );
    await page.evaluate(() => globalThis.__latentSky.setVariable("tcwv"));
    // The new stacks decode off the frame path; poll briefly rather than racing them.
    let tcwvChanged = 0;
    for (let attempt = 0; attempt < 15; attempt++) {
      const frameT = await sample();
      tcwvChanged = frame1.filter((p, i) => p.join(",") !== frameT[i].join(",")).length;
      if (tcwvChanged > 5) break;
      await new Promise((r) => setTimeout(r, 200));
    }
    check(tcwvChanged > 5, "switching to tcwv renders the global layer", `${tcwvChanged}/${frame1.length} sample points changed`);

    if (fixture.hero) {
      // The way down exists and is offered.
      const invites = await page.locator(".invite").count();
      check(invites === 1, "invitation present in orbit", `${invites} .invite element(s)`);
      const strips = await page.locator(".coming").count();
      check(strips === 0, "pre-forecast strip absent when a hero ships", `${strips} .coming element(s)`);
    } else {
      // No dead or dangling hero UI: nothing invites, marks, names or wipes.
      const counts = await page.evaluate(() => ({
        invite: document.querySelectorAll(".invite").length,
        marker: document.querySelectorAll(".hero-marker").length,
        place: document.querySelectorAll(".place").length,
        reveal: document.querySelectorAll(".reveal-overlay").length,
        fineToggle: document.querySelectorAll(".fine-toggle").length,
        returnBtn: document.querySelectorAll(".return").length,
      }));
      const dangling = Object.entries(counts).filter(([, n]) => n !== 0);
      check(
        dangling.length === 0,
        "no hero UI rendered (invitation, marker, place label, reveal, toggle, return)",
        dangling.map(([k, n]) => `${k}=${n}`).join(", "),
      );

      const stripText = await page.evaluate(
        () => document.querySelector(".coming")?.textContent?.trim() ?? null,
      );
      check(
        stripText === "Kilometre-scale AI detail arrives with the first forecast run",
        "quiet pre-forecast strip present with the built-in copy",
        JSON.stringify(stripText),
      );

      // The test hook exposes enterStorm unconditionally — with no hero it
      // must be a silent no-op, never a throw or a flight to nowhere.
      const view = await page.evaluate(() => {
        globalThis.__latentSky.enterStorm();
        return globalThis.__latentSky.getView();
      });
      check(
        view.view === "orbit" && view.flying === false,
        "enterStorm() is a safe no-op without a hero",
        `view=${view.view} flying=${view.flying}`,
      );
    }

    const naturalEarth = allRequests.filter((u) => u.includes("NaturalEarthII"));
    check(
      naturalEarth.length === 0,
      "manifest basemap in use — zero NaturalEarthII fallback requests",
      naturalEarth.slice(0, 3).join(", "),
    );

    const cesiumIon = externalRequests.filter((u) => u.includes("api.cesium.com"));
    check(cesiumIon.length === 0, "zero requests to api.cesium.com", cesiumIon.join(", "));
    check(
      externalRequests.length === 0,
      "zero requests to any non-localhost host",
      externalRequests.slice(0, 5).join(", "),
    );
    check(badResponses.length === 0, "zero responses >= 400", badResponses.slice(0, 5).join(", "));
    check(
      consoleErrors.length === 0,
      "zero console errors / failed asserts",
      consoleErrors.slice(0, 5).join(" | "),
    );
    check(pageErrors.length === 0, "zero page errors", pageErrors.slice(0, 5).join(" | "));
  } catch (err) {
    console.error(`\n[${fixture.name}] SUITE ABORTED:`, err);
    if (consoleErrors.length) console.error("console errors:", consoleErrors);
    if (pageErrors.length) console.error("page errors:", pageErrors);
    if (badResponses.length) console.error("bad responses:", badResponses);
    failures.push(`[${fixture.name}] suite aborted: ${err}`);
  } finally {
    await page.close();
  }
}

let exitCode = 1;
try {
  for (const fixture of FIXTURES) {
    await runFixture(fixture);
  }
  exitCode = failures.length === 0 ? 0 : 1;
  console.log(
    failures.length === 0
      ? `\nSMOKE TEST PASSED (${FIXTURES.length} fixtures)`
      : `\nSMOKE TEST FAILED: ${failures.length} check(s): ${failures.join("; ")}`,
  );
} finally {
  await browser.close();
  await server.close();
}
process.exit(exitCode);
