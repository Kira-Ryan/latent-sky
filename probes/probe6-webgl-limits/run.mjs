/**
 * Probe 6 driver — real Chrome on the target machine, reports WebGL2 limits
 * and proves the texture-array allocations the client design bounds against.
 *   cd probes/probe3-colour-identity && node ../probe6-webgl-limits/run.mjs
 * (runs from probe3's dir so it reuses that node_modules)
 */
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { pathToFileURL } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(join(process.cwd(), "package.json"));
const { chromium } = require("playwright-core");

const HERE = dirname(fileURLToPath(import.meta.url));
const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage();
await page.goto(pathToFileURL(join(HERE, "probe.html")).href);
const r = await page.evaluate(() => window.runProbe());
await browser.close();

console.log("renderer:", r.limits.renderer);
for (const [k, v] of Object.entries(r.limits)) {
  if (k !== "renderer") console.log(`  ${k.padEnd(28)} ${v}`);
}
console.log("allocations:");
for (const [k, v] of Object.entries(r.alloc)) console.log(`  ${k.padEnd(28)} ${v}`);
console.log(`two-layer mix() draw: readback ${r.draw.readback} (expected ~${r.draw.expected}) — ${r.draw.pass ? "PASS" : "FAIL"}`);
if (r.errors.length) { console.log("errors:", r.errors); }
console.log(r.ok ? "\nPROBE 6 PASSED" : "\nPROBE 6 FAILED");
process.exit(r.ok ? 0 : 1);
