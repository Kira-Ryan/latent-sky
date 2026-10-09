/**
 * Report test: the verification report's words, driven by real Chrome.
 *
 * The report (pipeline/tools/fss_report.html) derives every sentence it prints
 * from the results JSON embedded in it, in the browser, so the only way to test
 * what a reader is told is to render it. This renders the template the way
 * fss_report.py does, with the Dixie case study's committed results and with
 * variants of them built here, and checks the sentences that have been wrong:
 *
 *   - initiation timing in all four cases: both fields timed, one field only (a
 *     false alarm or a miss, which until 9 Oct 2026 the report called "no
 *     storms"), neither, and an onset a radar gap may have hidden;
 *   - an hour with no radar (an archive gap) left out of the intensity figure
 *     instead of poisoning it with NaN;
 *   - the separation chart: an undefined hour is a break, never a point at 0 km;
 *   - every chart's axis reaches its largest value instead of drawing it off the
 *     top (the axes were sized for Dixie, and daily runs go further).
 *
 *   node tests/report.spec.mjs
 *   CHANNEL=chromium node tests/report.spec.mjs     (CI: Playwright's Chromium)
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const REPO = dirname(dirname(dirname(fileURLToPath(import.meta.url))));
const TEMPLATE = readFileSync(join(REPO, "pipeline/tools/fss_report.html"), "utf8");
const DIXIE = readFileSync(join(REPO, "data/verification/dixie_2025_pre.fss.json"), "utf8");
const CHANNEL = process.env.CHANNEL || "chrome";

// fss_report.py embeds Python's json.dumps, which writes NaN as a bare literal, and
// the template relies on it: NaN breaks a line where null would plot as 0. JSON
// cannot carry NaN, so the variants are parsed and written back with it restored.
const NAN = "__NaN__";
const load = () => JSON.parse(DIXIE.replace(/\bNaN\b/g, `"${NAN}"`), (k, v) => (v === NAN ? NaN : v));
const embed = (R) => JSON.stringify(R, (k, v) => (Number.isNaN(v) ? NAN : v)).replaceAll(`"${NAN}"`, "NaN");

/** Blank one hour the way verify.score() records an hour with no radar file. */
function noRadar(R, lead) {
  for (const block of [R, ...Object.values(R.baselines || {})]) {
    const row = block.leads.find((r) => r.lead_h === lead);
    row.no_radar = true;
    row.valid_cells = 0;
    row.centroid_40dbz = { forecast: null, observed: null, separation_km: null };
    row.max_dbz.observed = NaN;
    for (const [thr, e] of Object.entries(row.fss)) {
      e.by_window = e.by_window.map(() => NaN);
      e.obs_base_rate = e.fss_useful = e.fss_random = NaN;
      if (e.ensemble_by_window) {
        e.ensemble_by_window = e.ensemble_by_window.map(() => NaN);
        e.member_by_window = e.member_by_window.map((m) => m.map(() => NaN));
      }
      const c = row.coverage[thr];
      c.forecast = c.observed = NaN;
      if (c.members) c.members = c.members.map(() => NaN);
    }
  }
  R.event.mrms_missing = [...(R.event.mrms_missing || []), R.leads.find((r) => r.lead_h === lead).valid];
  return R;
}
/** Take one field's strong convection away: 40 dBZ coverage zero in every hour. */
function without(R, field) {
  for (const r of R.leads) r.coverage["40"][field] = 0;
  return R;
}

const failures = [];
const browser = await chromium.launch({ channel: CHANNEL });

async function render(name, R) {
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setContent(TEMPLATE.replace("__RESULTS__", embed(R)), { waitUntil: "load" });
  const out = await page.evaluate(() => {
    const tile = (k) => {
      const t = [...document.querySelectorAll("#tiles .tile")].find((e) => e.querySelector(".k").textContent === k);
      return { v: t.querySelector(".v").textContent, s: t.querySelector(".s").textContent };
    };
    const svg = document.querySelector("#c-sep svg");
    const line = [...svg.querySelectorAll("path")].find((e) => e.getAttribute("stroke") === "var(--accent)");
    const pts = [...line.getAttribute("d").matchAll(/([ML])([\d.-]+) ([\d.-]+)/g)].map((m) => ({ cmd: m[1], y: +m[3] }));
    const axisY = +svg.querySelector(".axis line").getAttribute("y1");
    const ticks = [...svg.querySelectorAll("text")].map((t) => t.textContent).filter((s) => / km$/.test(s));
    const chart = (id) => {
      const c = document.querySelector(`#${id} svg`);
      const ys = [...c.querySelectorAll('path[fill="none"]')].flatMap((e) => [...(e.getAttribute("d") || "").matchAll(/[ML][\d.-]+ ([\d.-]+)/g)].map((m) => +m[1]));
      const t = [...c.querySelectorAll("text")].map((e) => e.textContent).filter((x) => /%$/.test(x));
      return { offTop: ys.filter((y) => y < 0).length, topTick: parseFloat(t[t.length - 1]) };
    };
    return {
      init: tile("Initiation timing"),
      intensity: tile("Core intensity"),
      timing: [...document.querySelectorAll("#reading p")].map((p) => p.textContent).find((t) => t.startsWith("Timing")) ?? "",
      sep: {
        segments: pts.filter((p) => p.cmd === "M").length,
        atZero: pts.filter((p) => Math.abs(p.y - axisY) < 0.5).length,
        offTop: pts.filter((p) => p.y < 0).length,
        topTick: parseFloat(ticks[ticks.length - 1]),
      },
      cov40: chart("c-cov40"),
      cov20: chart("c-cov20"),
    };
  });
  await page.close();
  console.log(`\n${name}`);
  const check = (label, ok, detail) => {
    console.log(`${ok ? "  PASS" : "  FAIL"}  ${label}${ok ? "" : ` — ${detail}`}`);
    if (!ok) failures.push(`[${name}] ${label}`);
  };
  check("no page errors", errors.length === 0, errors.join(" | "));
  return { out, check };
}

try {
  {
    const { out, check } = await render("Dixie as published", load());
    check("initiation timed, same hour", out.init.v === "same hour" && /at 21Z \(observed\) and 21Z \(forecast\)/.test(out.init.s), JSON.stringify(out.init));
    check("intensity +6.8 dBZ", out.intensity.v === "+6.8 dBZ", out.intensity.v);
    check("separation one unbroken line on the 0-300 km axis", out.sep.segments === 1 && out.sep.offTop === 0 && out.sep.topTick === 300, JSON.stringify(out.sep));
    check("coverage axes as designed, 1.5% and 10%", out.cov40.topTick === 1.5 && out.cov20.topTick === 10 && out.cov40.offTop + out.cov20.offTop === 0, JSON.stringify([out.cov40, out.cov20]));
  }
  {
    const { out, check } = await render("Dixie, no radar at 21Z (before the onset)", noRadar(load(), 3));
    check("initiation not known", out.init.v === "not known" && /no radar at 21Z, so either onset may have been earlier/.test(out.init.s), JSON.stringify(out.init));
    check("the gap breaks the separation line and is not a 0 km point", out.sep.segments === 2 && out.sep.atZero === 0, JSON.stringify(out.sep));
  }
  {
    const { out, check } = await render("Dixie, no radar at 22Z (after the onset)", noRadar(load(), 4));
    check("initiation still timed: a later gap hides nothing", out.init.v === "same hour", JSON.stringify(out.init));
  }
  {
    const { out, check } = await render("Dixie, no radar at 06Z (+12 h)", noRadar(load(), 12));
    check("intensity left the gap out, not NaN", /^[+-]\d+\.\d dBZ$/.test(out.intensity.v) && /1 hour with no radar left out/.test(out.intensity.s), JSON.stringify(out.intensity));
  }
  {
    const { out, check } = await render("Forecast only: the radar never reaches 0.1% at 40 dBZ", without(load(), "observed"));
    check("a false alarm, not 'no storms'", out.init.v === "forecast only" && /false alarm/.test(out.init.s), JSON.stringify(out.init));
  }
  {
    const { out, check } = await render("Radar only: the forecast never reaches 0.1% at 40 dBZ", without(load(), "forecast"));
    check("a miss, not 'no storms'", out.init.v === "radar only" && /a miss/.test(out.init.s), JSON.stringify(out.init));
  }
  {
    const R = load();
    R.event.narrative = "generic";
    const { out, check } = await render("Neither field, generic reading, one gap", noRadar(without(without(R, "observed"), "forecast"), 3));
    check("no storms, with the gap said", out.init.v === "no storms" && /1 hour with no radar left out/.test(out.init.s), JSON.stringify(out.init));
    check("the reading's timing agrees", /^Timing\. Neither the radar nor the forecast/.test(out.timing) && /1 hour with no radar left out/.test(out.timing), out.timing);
  }
  {
    const R = load();
    R.leads.find((r) => r.lead_h === 10).centroid_40dbz.separation_km = 900;
    const { out, check } = await render("A 900 km separation", R);
    check("the axis reaches it", out.sep.offTop === 0 && out.sep.topTick >= 750, JSON.stringify(out.sep));
  }
  {
    const R = load();
    R.leads.find((r) => r.lead_h === 10).coverage["20"].forecast = 0.179;
    R.leads.find((r) => r.lead_h === 10).coverage["40"].observed = 0.0157;
    const { out, check } = await render("A wet day: 17.9% at 20 dBZ, 1.57% at 40 dBZ", R);
    check("both coverage axes reach it", out.cov20.offTop + out.cov40.offTop === 0 && out.cov20.topTick >= 17.5 && out.cov40.topTick >= 1.57, JSON.stringify([out.cov40, out.cov20]));
  }
} finally {
  await browser.close();
}

if (failures.length) {
  console.log(`\n${failures.length} FAILED:\n  ${failures.join("\n  ")}`);
  process.exit(1);
}
console.log("\nall report checks passed");
