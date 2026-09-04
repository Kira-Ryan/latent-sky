/**
 * Verify how a run states its own lifecycle: when it was issued, how far ahead
 * the frame in view looks, and where it stands in the scoring loop.
 *
 * Three manifests, three states, all real data:
 *   pending   a live daily run, published before the weather, not yet scored
 *   scored    a case study that has been measured against radar, with a report
 *   no-claim  a typhoon nothing will ever score — the UI must say NOTHING
 *
 * The last is the one worth having a test for. Silence is a deliberate state,
 * and the easy regression is a reassuring default that promises a verification
 * no pipeline is going to produce.
 *
 *   node tests/verify-forecast-state.mjs [outDir]
 */
import { mkdir } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const WEB = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT_DIR = resolve(process.argv[2] ?? join(WEB, "tests", "shots"));
const PORT = 8641;

const CASES = [
  {
    name: "pending",
    manifest: "/data/web/daily/2026-09-02/manifest.json",
    label: "live daily run, not yet scored",
    expect: {
      masthead: /^Forecast issued \d{2} \w{3,4} \d{4}, \d{2}:\d{2} UTC/,
      verification: /^Not yet scored\./,
      lead: /· \+10 h$/,
      frameZero: /· analysis$/,
      invite: /^Central US · enter the storm$/,
      noReportLink: true,
    },
  },
  {
    name: "scored",
    manifest: "/data/web/dixie/manifest.json",
    label: "case study, scored against radar",
    expect: {
      masthead: /^Forecast issued 14 Mar 2025, 18:00 UTC/,
      verification: /^Scored against MRMS radar\.\s*Read the verification\.$/,
      lead: /· \+10 h$/,
      frameZero: /· analysis$/,
      noReportLink: false,
    },
  },
  {
    name: "no-claim",
    manifest: "/data/web/taiwan/manifest.json",
    label: "typhoon, nothing scoring it",
    expect: {
      verification: null, // the line must be absent entirely
      noReportLink: true,
    },
  },
];

await mkdir(OUT_DIR, { recursive: true });
const server = await createServer({ root: WEB, server: { port: PORT, strictPort: true }, logLevel: "warn" });
await server.listen();
const browser = await chromium.launch({ channel: process.env.CHANNEL || "chrome", args: ["--enable-unsafe-swiftshader"] });

const problems = [];
for (const c of CASES) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 820 } });
  page.on("pageerror", (e) => problems.push(`${c.name}: ${e}`));
  page.on("console", (m) => { if (m.type() === "error") problems.push(`${c.name}: ${m.text()}`); });
  await page.goto(`http://localhost:${PORT}/?manifest=${c.manifest}&test=1`, { waitUntil: "load" });
  await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });
  await page.waitForTimeout(900);

  const read = () =>
    page.evaluate(() => ({
      masthead: document.querySelector(".runid")?.textContent?.replace(/\s+/g, " ").trim() ?? "",
      invite: document.querySelector(".invite")?.textContent?.replace(/\s+/g, " ").trim() ?? "",
      verification: document.querySelector(".verification")
        ? document.querySelector(".verification").textContent.replace(/\s+/g, " ").trim()
        : null,
      reportLink: !!document.querySelector(".verification a"),
      time: document.querySelector(".scrubber .time")?.textContent?.replace(/\s+/g, " ").trim() ?? "",
      aria: document.querySelector(".scrubber input[type=range]")?.getAttribute("aria-valuetext") ?? "",
    }));

  await page.evaluate(() => globalThis.__latentSky.setFrame(10));
  await page.waitForTimeout(300);
  const at10 = await read();
  await page.evaluate(() => globalThis.__latentSky.setFrame(0));
  await page.waitForTimeout(300);
  const at0 = await read();

  console.log(`\n=== ${c.name} — ${c.label} ===`);
  console.log(`  masthead     ${at10.masthead}`);
  console.log(`  verification ${at10.verification ?? "(absent — no claim made)"}`);
  console.log(`  frame 10     ${at10.time}`);
  console.log(`  frame 0      ${at0.time}`);
  console.log(`  aria         ${at10.aria}`);
  if (at10.invite) console.log(`  invitation   ${at10.invite}`);

  const e = c.expect;
  const check = (cond, msg) => { if (!cond) problems.push(`${c.name}: ${msg}`); };
  if (e.masthead) check(e.masthead.test(at10.masthead), `masthead "${at10.masthead}" !~ ${e.masthead}`);
  if (e.verification === null) check(at10.verification === null, `verification line must be absent, got "${at10.verification}"`);
  else if (e.verification) check(at10.verification && e.verification.test(at10.verification), `verification "${at10.verification}" !~ ${e.verification}`);
  if (e.lead) check(e.lead.test(at10.time), `frame 10 "${at10.time}" !~ ${e.lead}`);
  if (e.frameZero) check(e.frameZero.test(at0.time), `frame 0 "${at0.time}" !~ ${e.frameZero}`);
  if (e.invite) check(e.invite.test(at10.invite), `invitation "${at10.invite}" !~ ${e.invite}`);
  check(at10.reportLink === !e.noReportLink, `report link presence wrong (got ${at10.reportLink})`);
  // The spoken form must never leave a screen-reader user with a bare instant.
  if (e.lead) check(/after initialisation/.test(at10.aria), `aria "${at10.aria}" omits the lead time`);
  if (e.frameZero) {
    const aria0 = at0.aria;
    check(/analysis/.test(aria0), `aria at frame 0 "${aria0}" does not name the analysis`);
  }

  await page.screenshot({ path: join(OUT_DIR, `state-${c.name}.png`) });
  await page.close();
}

await browser.close();
await server.close();
if (problems.length) {
  console.error("\nFORECAST STATE VERIFY FAILED:");
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log("\nforecast state verify complete — issue time, lead time and scoring state all correct, silence where no claim is owed.");
