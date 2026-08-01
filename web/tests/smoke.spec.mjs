/**
 * Smoke test — probe3 style: plain node script driving REAL Chrome via
 * playwright-core (channel "chrome"), no playwright test-runner.
 *
 * Serves the app with the Vite dev server against the committed synthetic
 * fixture (web/dev-fixture/), then asserts:
 *   1. the canvas is not a uniform colour (the globe and field rendered)
 *   2. scrubbing one frame forward changes pixels
 *   3. zero console errors / failed console.asserts / page errors
 *   4. zero responses >= 400
 *   5. zero requests to api.cesium.com or ANY non-localhost host
 *
 * Exits non-zero on any failure.
 *
 *   node tests/smoke.spec.mjs
 */
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // web/
const PORT = 8323;

if (!existsSync(join(ROOT, "dev-fixture", "manifest.json"))) {
  console.error("fixture missing — run `npm run fixture` first");
  process.exit(1);
}

const failures = [];
const check = (ok, label, detail = "") => {
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${label}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures.push(label);
};

const server = await createServer({
  root: ROOT,
  server: { port: PORT, strictPort: true },
  logLevel: "warn",
});
await server.listen();

const browser = await chromium.launch({
  channel: "chrome",
  args: ["--enable-unsafe-swiftshader"], // deterministic software GL if no hw context
});
const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });

const consoleErrors = [];
const pageErrors = [];
const badResponses = [];
const externalRequests = [];
page.on("console", (m) => {
  if (m.type() === "error" || m.type() === "assert") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => pageErrors.push(String(e)));
page.on("response", (r) => {
  if (r.status() >= 400) badResponses.push(`${r.status()} ${r.url()}`);
});
page.on("request", (r) => {
  const host = new URL(r.url()).hostname;
  if (!["localhost", "127.0.0.1", "[::1]"].includes(host)) externalRequests.push(r.url());
});

let exitCode = 1;
try {
  await page.goto(`http://localhost:${PORT}/?manifest=/dev-fixture/manifest.json&test=1`, {
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

  exitCode = failures.length === 0 ? 0 : 1;
  console.log(
    failures.length === 0
      ? "\nSMOKE TEST PASSED"
      : `\nSMOKE TEST FAILED: ${failures.length} check(s): ${failures.join("; ")}`,
  );
} catch (err) {
  console.error("\nSMOKE TEST ABORTED:", err);
  if (consoleErrors.length) console.error("console errors:", consoleErrors);
  if (pageErrors.length) console.error("page errors:", pageErrors);
  if (badResponses.length) console.error("bad responses:", badResponses);
  exitCode = 1;
} finally {
  await browser.close();
  await server.close();
}
process.exit(exitCode);
