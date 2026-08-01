/**
 * Probe 3 driver — serves the probe page and drives real Chrome via playwright-core.
 *
 * Uses the system Chrome (channel: "chrome") rather than a downloaded chromium, so the
 * WebGL2 pipeline under test is the one an actual visitor would get.
 *
 *   npm install && node run.mjs
 */
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const ROOT = dirname(fileURLToPath(import.meta.url)); // import.meta.dirname needs Node 21+
const CESIUM = join(ROOT, "node_modules", "cesium", "Build", "Cesium");
const PORT = 8321;

const MIME = {
  ".html": "text/html", ".js": "text/javascript", ".css": "text/css",
  ".json": "application/json", ".png": "image/png", ".jpg": "image/jpeg",
  ".ktx2": "image/ktx2", ".wasm": "application/wasm", ".glb": "model/gltf-binary",
  ".xml": "application/xml", ".svg": "image/svg+xml",
};

const server = createServer(async (req, res) => {
  const url = decodeURIComponent(req.url.split("?")[0]);
  const path = url.startsWith("/cesium/")
    ? join(CESIUM, normalize(url.slice(8)))
    : join(ROOT, normalize(url === "/" ? "probe.html" : url));
  try {
    const body = await readFile(path);
    res.writeHead(200, { "content-type": MIME[extname(path)] ?? "application/octet-stream" });
    res.end(body);
  } catch {
    console.log(`  [404] ${url}`);
    res.writeHead(404).end("not found");
  }
});
await new Promise(r => server.listen(PORT, r));

const browser = await chromium.launch({
  channel: "chrome",
  args: ["--enable-unsafe-swiftshader"], // deterministic software GL if no hw context
});
const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
const problems = [];
page.on("console", m => { if (m.type() === "error" || m.type() === "assert") problems.push(m.text()); });
page.on("pageerror", e => problems.push(String(e)));
page.on("response", res => { if (res.status() >= 400) problems.push(`${res.status()} ${res.url()}`); });

await page.goto(`http://localhost:${PORT}/probe.html`, { waitUntil: "load" });
const r = await page.evaluate(() => window.runProbe());

const rgb = a => `(${a.map(v => String(v).padStart(3)).join(",")})`;
const mark = ok => (ok ? "IDENTICAL" : "DIFFERENT  <-- contamination");

console.log(`\nCesiumJS ${r.meta.cesium}   renderer defaults:`);
for (const [k, v] of Object.entries(r.meta.defaults)) console.log(`  ${k.padEnd(22)} ${v}`);

console.log("\n=== TEST 1/2 — same authored bytes, same canvas pixel, two framings ===");
console.log("mode      colour  authored        globe-zoom      hero-zoom       delta          verdict");
for (const x of r.sameCanvasPixel) {
  console.log(
    `${x.mode.padEnd(9)} ${x.colour.padEnd(6)} ${rgb(x.authored)}  ${rgb(x.globe)}  ` +
    `${rgb(x.hero)}  ${rgb(x.delta)}  ${mark(x.identical)}`
  );
}

console.log("\n=== TEST 3 — split symmetry, identical colour either side of the divider ===");
console.log("mode      view    left            right           delta          verdict");
for (const x of r.splitSymmetry) {
  console.log(
    `${x.mode.padEnd(9)} ${x.view.padEnd(6)} ${rgb(x.left)}  ${rgb(x.right)}  ` +
    `${rgb(x.delta)}  ${mark(x.identical)}`
  );
}

if (problems.length) {
  console.log("\nconsole errors / failed assertions:");
  for (const p of problems) console.log("  " + p);
}

const defaultsBroken = r.sameCanvasPixel.filter(x => x.mode === "defaults" && !x.identical).length;
const fixedBroken = r.sameCanvasPixel.filter(x => x.mode === "fixed" && !x.identical).length;
console.log(
  `\nSUMMARY: ${defaultsBroken}/2 default configs contaminated, ` +
  `${fixedBroken}/2 fixed configs contaminated.`
);

await browser.close();
server.close();
process.exit(fixedBroken > 0 ? 1 : 0);
