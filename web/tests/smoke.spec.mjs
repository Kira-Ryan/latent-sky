/**
 * Smoke test — probe3 style: plain node script driving REAL Chrome via
 * playwright-core (channel "chrome"), no playwright test-runner.
 *
 * Serves the app with the Vite dev server and runs THREE scenarios against the
 * committed synthetic fixtures (web/dev-fixture/):
 *
 *   A. manifest.json            with-hero — hero pairs + global layers + basemap
 *   B. manifest-hero-free.json  hero-free — global layers only, the public
 *                               pre-forecast state the site first deploys in
 *   C. catalogue.json           the EVENT SWITCHER — boot from a catalogue,
 *                               switch between the two manifests above, and
 *                               prove the globe fully re-initialises. Also
 *                               covers the ?event= deep link and the one-event
 *                               catalogue, where the switcher must not exist.
 *
 * A and B drive ?manifest= directly, which bypasses the catalogue entirely —
 * that path is the site's behaviour before any catalogue exists, so it must
 * keep passing unchanged.
 *
 * Shared assertions, per fixture:
 *   1. the canvas is not a uniform colour (the globe and field rendered)
 *   2. scrubbing one frame forward changes pixels
 *   3. tcwv (a global-only layer) is selectable and renders differently
 *   4. the scrubber spans exactly the manifest's frames (frame-count agnostic)
 *   5. the zoom floor policy: 8,000 km hero-free, default freedom with a hero
 *   6. the manifest basemap is used — zero NaturalEarthII fallback requests
 *   7. zero console errors / failed console.asserts / page errors
 *   8. zero responses >= 400
 *   9. zero requests to api.cesium.com or ANY non-localhost host
 *
 * With-hero only:
 *  10. the invitation ("enter the storm") is present in orbit
 *  11. the pre-forecast strip is absent — a shipped hero IS the way down
 *
 * Hero-free only:
 *  10. no invitation, no hero marker, no place label, no reveal overlay,
 *      no "Generated detail" toggle — no dead or dangling hero UI
 *  11. the quiet pre-forecast strip is present with the built-in copy
 *  12. __latentSky.enterStorm() is a safe no-op (still orbit, nothing thrown)
 *
 * Catalogue scenario (C), on top of the shared error/network checks:
 *   - the switcher renders, names the default event, and is keyboard-operable
 *   - ?event= is written at boot and rewritten on every switch (replaceState)
 *   - switching re-renders the globe (pixels change) and re-initialises it:
 *     frame count, scrubber range, camera zoom floor, hero chrome and the
 *     caveat copy all follow the NEW manifest, and the imagery-layer count is
 *     exactly base + stacks — no layer survives a switch
 *   - a ?event= deep link opens that event directly
 *   - a one-event catalogue renders NO switcher at all
 *   - NO catalogue at all falls back to /data/web/manifest.json, logged, with
 *     the catalogue's own 404 the only >= 400 in the run
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
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

const ROOT = dirname(dirname(fileURLToPath(import.meta.url))); // web/
// Overridable because Windows reserves shifting port ranges for Hyper-V:
// 8323 fell inside 8241-8340 on this machine on 12 Sep 2026 and the server
// could not bind (EACCES, with nothing actually listening). CI keeps the default.
const PORT = Number(process.env.SMOKE_PORT ?? 8323);

const FIXTURES = [
  { name: "with-hero", manifest: "/dev-fixture/manifest.json", hero: true },
  { name: "hero-free", manifest: "/dev-fixture/manifest-hero-free.json", hero: false },
];

/** Scenario C's catalogues, and the two events they index. */
const CATALOGUE = "/dev-fixture/catalogue.json";
const CATALOGUE_SINGLE = "/dev-fixture/catalogue-single.json";
const HERO_EVENT = {
  id: "synthetic-hero",
  title: "Synthetic vortex (hero pair)",
  runId: "dev-fixture-synthetic",
  frameCount: 16,
  minZoom: 1,
  // base layer + wind10m {global, hero-coarse, hero-fine} x 3 cross-fade slots.
  imageryLayers: 10,
};
const GLOBAL_EVENT = {
  id: "synthetic-global",
  title: "Synthetic global reanalysis",
  runId: "dev-fixture-synthetic-hero-free",
  frameCount: 11,
  minZoom: 8_000_000,
  // base layer + wind10m global x 3 slots.
  imageryLayers: 4,
};

for (const path of [...FIXTURES.map((f) => f.manifest), CATALOGUE, CATALOGUE_SINGLE]) {
  const file = join(ROOT, ...path.split("/").filter(Boolean));
  if (!existsSync(file)) {
    console.error(`fixture missing: ${file} — run \`npm run fixture\` first`);
    process.exit(1);
  }
}

const failures = [];

/**
 * Manifests derived from the committed hero fixture with EVERY frame pointed at a
 * path that does not exist, served from memory so nothing derived is written into
 * dev-fixture/. Two shapes of "absent", because production has both:
 *   404  ../data/web/... -> a real 404 from the dev data middleware (S3 semantics)
 *   spa  a path under /dev-fixture/ -> Vite answers 200 with index.html, exactly
 *        what the CloudFront 403/404 -> /index.html mapping does on the live site,
 *        so the "frame" is HTML that fails to decode.
 */
function missingFrameManifests() {
  const base = JSON.parse(readFileSync(join(ROOT, "dev-fixture", "manifest.json"), "utf8"));
  const derive = (prefix) => {
    const m = structuredClone(base);
    for (const layer of Object.values(m.layers)) layer.frames = layer.frames.map((f) => prefix + f);
    return JSON.stringify(m);
  };
  // An archived daily run at the permanent address the app derives, and a
  // catalogue that has rolled it off — the 12 Sep 2026 live defect, where
  // following the verification record's link to an older scored run opened the
  // newest run instead. Frames point back at the real fixture directory so the
  // run actually renders.
  const archived = structuredClone(base);
  archived.run = { ...archived.run, id: "daily-2026-09-02", init: "2026-09-02T12:00:00Z",
                   stormName: "Central US", verification: "scored",
                   reportUrl: "/verification/daily-2026-09-02.html" };
  archived.frames = archived.frames.map((_, i) =>
    new Date(Date.UTC(2026, 8, 2, 12 + i)).toISOString().replace(".000", ""));
  for (const layer of Object.values(archived.layers)) {
    layer.frames = layer.frames.map((f) => "../../../../dev-fixture/" + f);
    layer.lut = "../../../../dev-fixture/" + layer.lut;
  }
  for (const k of ["global", "hero"]) {
    if (archived.basemap?.[k]) archived.basemap[k] = "../../../../dev-fixture/" + archived.basemap[k];
  }
  const rolledOffCatalogue = {
    schemaVersion: 1,
    events: [
      { id: "synthetic-hero", title: "Synthetic vortex (hero pair)", subtitle: "the newest run",
        manifest: "../dev-fixture/manifest.json", kind: "hero", region: "conus", hasHero: true, default: true },
      { id: "synthetic-global", title: "Synthetic global reanalysis", subtitle: "another",
        manifest: "../dev-fixture/manifest-hero-free.json", kind: "global-only", region: "global",
        hasHero: false, default: false },
    ],
  };

  const routes = {
    "/dev-fixture/manifest-missing-404.json": derive("../data/web/__no_such_run__/"),
    "/dev-fixture/manifest-missing-spa.json": derive("__no_such_dir__/"),
    "/data/web/daily/2026-09-02/manifest.json": JSON.stringify(archived),
    "/dev-fixture/catalogue-rolled-off.json": JSON.stringify(rolledOffCatalogue),
  };
  return {
    name: "latent-sky-missing-frame-fixtures",
    // "pre", because vite.config's /data middleware ALWAYS answers (404 on a
    // miss, never next()), so a route under /data registered after it is
    // unreachable. Without this the archived-run route silently read the real
    // gitignored data/web/daily/<date>/manifest.json from disk instead.
    enforce: "pre",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const body = routes[(req.url ?? "").split("?")[0]];
        if (!body) return next();
        res.setHeader("content-type", "application/json");
        res.end(body);
      });
    },
  };
}

const server = await createServer({
  root: ROOT,
  server: { port: PORT, strictPort: true },
  logLevel: "warn",
  plugins: [missingFrameManifests()],
});
await server.listen();

const CHANNEL = process.env.CHANNEL || "chrome"; // "chromium" in CI (web.yml)
console.log(`browser channel: ${CHANNEL}`);
const browser = await chromium.launch({
  channel: CHANNEL,
  args: ["--enable-unsafe-swiftshader"], // deterministic software GL if no hw context
});

/** A labelled PASS/FAIL reporter that records into the shared failure list. */
function checker(scenario) {
  return (ok, label, detail = "") => {
    console.log(`${ok ? "  PASS" : "  FAIL"}  ${label}${detail ? ` — ${detail}` : ""}`);
    if (!ok) failures.push(`[${scenario}] ${label}`);
  };
}

/** Wire up the console/page-error/network recorders every scenario asserts on. */
function attachDiagnostics(page) {
  const diag = {
    consoleErrors: [],
    pageErrors: [],
    badResponses: [],
    externalRequests: [],
    allRequests: [],
  };
  page.on("console", (m) => {
    if (m.type() === "error" || m.type() === "assert") diag.consoleErrors.push(m.text());
  });
  page.on("pageerror", (e) => diag.pageErrors.push(String(e)));
  page.on("response", (r) => {
    if (r.status() >= 400) diag.badResponses.push(`${r.status()} ${r.url()}`);
  });
  page.on("request", (r) => {
    diag.allRequests.push(r.url());
    const host = new URL(r.url()).hostname;
    if (!["localhost", "127.0.0.1", "[::1]"].includes(host)) diag.externalRequests.push(r.url());
  });
  return diag;
}

/** Sample an even grid of RGB triples from the Cesium canvas. */
function sampleGrid(page) {
  return page.evaluate(async () => {
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
}

/**
 * How many of `base`'s sample points differ once the canvas has caught up.
 * The grids can differ in LENGTH across an event switch — the HUD's caveat copy
 * comes from the manifest, so a longer note wraps and the globe canvas resizes —
 * and a resized canvas is itself a re-render, so the surplus counts as changed.
 */
async function changedFrom(page, base, attempts = 15) {
  let changed = 0;
  for (let attempt = 0; attempt < attempts; attempt++) {
    const next = await sampleGrid(page);
    const shared = Math.min(base.length, next.length);
    changed = Math.abs(base.length - next.length);
    for (let i = 0; i < shared; i++) {
      if (base[i].join(",") !== next[i].join(",")) changed++;
    }
    if (changed > 5) return changed;
    await new Promise((r) => setTimeout(r, 200));
  }
  return changed;
}

/** The shared error/network assertions every scenario ends with. */
function checkClean(check, diag) {
  const naturalEarth = diag.allRequests.filter((u) => u.includes("NaturalEarthII"));
  check(
    naturalEarth.length === 0,
    "manifest basemap in use — zero NaturalEarthII fallback requests",
    naturalEarth.slice(0, 3).join(", "),
  );
  const cesiumIon = diag.externalRequests.filter((u) => u.includes("api.cesium.com"));
  check(cesiumIon.length === 0, "zero requests to api.cesium.com", cesiumIon.join(", "));
  check(
    diag.externalRequests.length === 0,
    "zero requests to any non-localhost host",
    diag.externalRequests.slice(0, 5).join(", "),
  );
  check(diag.badResponses.length === 0, "zero responses >= 400", diag.badResponses.slice(0, 5).join(", "));
  check(
    diag.consoleErrors.length === 0,
    "zero console errors / failed asserts",
    diag.consoleErrors.slice(0, 5).join(" | "),
  );
  check(diag.pageErrors.length === 0, "zero page errors", diag.pageErrors.slice(0, 5).join(" | "));
}

/** Run the full suite against one fixture on a fresh page. */
async function runFixture(fixture) {
  console.log(`\n=== fixture: ${fixture.name} (${fixture.manifest}) ===`);
  const check = checker(fixture.name);

  const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
  const diag = attachDiagnostics(page);
  const { consoleErrors, pageErrors, badResponses } = diag;

  try {
    await page.goto(`http://localhost:${PORT}/?manifest=${fixture.manifest}&test=1`, {
      waitUntil: "load",
    });
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, {
      timeout: 45_000,
    });
    console.log("app reported ready (manifest loaded, layers built, tiles loaded)\n");

    const sample = () => sampleGrid(page);

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
        stripText ===
          "Global view: 0.5° reanalysis · the forecasts in the menu carry kilometre-scale AI detail",
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

    // Presentation discipline: the scrubber must span exactly the manifest's
    // frames (frame-count agnosticism — the public manifest is moving 5 -> ~16
    // frames and the UI must simply follow), and the camera zoom floor is a
    // hero-free-only constraint: 8,000 km with no hero (0.5° mush below, tuned
    // by eye — flight.ts HERO_FREE_MIN_ZOOM), the Cesium default (1 m) when a
    // hero pair rewards the descent.
    const scrub = await page.evaluate(() => ({
      max: document.querySelector(".scrubber input[type=range]")?.max ?? null,
      frameCount: globalThis.__latentSky.frameCount,
      minZoom: document.querySelector(".globe")?.cesiumWidget?.scene
        .screenSpaceCameraController.minimumZoomDistance,
    }));
    check(
      scrub.max === String(scrub.frameCount - 1) && scrub.frameCount >= 3,
      "scrubber spans the manifest's frames",
      `range max=${scrub.max}, frameCount=${scrub.frameCount}`,
    );
    const expectedMinZoom = fixture.hero ? 1 : 8_000_000;
    check(
      scrub.minZoom === expectedMinZoom,
      fixture.hero
        ? "hero manifest keeps full zoom freedom (default minimumZoomDistance)"
        : "hero-free manifest floors the camera at 8,000 km",
      `minimumZoomDistance=${scrub.minZoom}`,
    );

    // ?manifest= bypasses the catalogue, so there is no event index and
    // therefore no switcher — the pre-catalogue behaviour, unchanged.
    const switchers = await page.locator(".switcher").count();
    check(switchers === 0, "no event switcher in single-manifest mode", `${switchers} .switcher element(s)`);

    checkClean(check, diag);
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

/** Everything the app exposes about the state a switch is supposed to reset. */
function readState(page) {
  return page.evaluate(() => {
    const hook = globalThis.__latentSky;
    const widget = document.querySelector(".globe")?.cesiumWidget;
    return {
      activeEventId: hook.activeEventId,
      frameCount: hook.frameCount,
      heroAvailable: hook.heroAvailable,
      variables: hook.variables,
      scrubberMax: document.querySelector(".scrubber input[type=range]")?.max ?? null,
      minZoom: widget?.scene.screenSpaceCameraController.minimumZoomDistance ?? null,
      imageryLayers: widget?.scene.imageryLayers.length ?? null,
      splitPosition: widget?.scene.splitPosition ?? null,
      trigger: document.querySelector(".switcher .trigger-title")?.textContent?.trim() ?? null,
      runId: document.querySelector(".runid")?.textContent?.trim() ?? null,
      caveat: document.querySelector(".caveat-text")?.textContent?.trim() ?? null,
      invite: document.querySelectorAll(".invite").length,
      coming: document.querySelectorAll(".coming").length,
      marker: document.querySelectorAll(".hero-marker").length,
      place: document.querySelectorAll(".place").length,
      reveal: document.querySelectorAll(".reveal-overlay").length,
    };
  });
}

/** Assert one event is fully and exclusively loaded — nothing of the other left. */
function checkEventLoaded(check, state, want, label) {
  check(state.activeEventId === want.id, `${label}: active event is ${want.id}`, `got ${state.activeEventId}`);
  check(
    state.runId === want.runId,
    `${label}: the loaded manifest is ${want.runId}`,
    JSON.stringify(state.runId),
  );
  check(
    state.frameCount === want.frameCount && state.scrubberMax === String(want.frameCount - 1),
    `${label}: time reset to this event's ${want.frameCount} frames`,
    `frameCount=${state.frameCount} scrubberMax=${state.scrubberMax}`,
  );
  check(
    state.minZoom === want.minZoom,
    `${label}: camera zoom floor re-applied (${want.minZoom} m)`,
    `minimumZoomDistance=${state.minZoom}`,
  );
  // The strongest no-stale-layers assertion available: an exact count of the
  // imagery collection. A leaked stack from the previous event shows up here
  // long before it shows up as a wrong pixel.
  check(
    state.imageryLayers === want.imageryLayers,
    `${label}: imagery collection is exactly base + this event's stacks (${want.imageryLayers})`,
    `imageryLayers=${state.imageryLayers}`,
  );
  const heroWanted = want.id === HERO_EVENT.id;
  check(
    state.heroAvailable === heroWanted &&
      state.invite === (heroWanted ? 1 : 0) &&
      state.coming === (heroWanted ? 0 : 1) &&
      (heroWanted || (state.marker === 0 && state.place === 0 && state.reveal === 0)),
    `${label}: hero UI ${heroWanted ? "present" : "absent"}, matching the manifest`,
    `heroAvailable=${state.heroAvailable} invite=${state.invite} coming=${state.coming} ` +
      `marker=${state.marker} place=${state.place} reveal=${state.reveal}`,
  );
}

/**
 * Scenario C — the event switcher: boot from a catalogue, switch, and prove
 * the globe re-initialises rather than accumulating the previous event.
 */
async function runCatalogue() {
  const name = "catalogue";
  console.log(`\n=== scenario: ${name} (${CATALOGUE}) ===`);
  const check = checker(name);

  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const diag = attachDiagnostics(page);

  try {
    await page.goto(`http://localhost:${PORT}/?catalogue=${CATALOGUE}&test=1`, { waitUntil: "load" });
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 45_000 });
    console.log("app reported ready (catalogue loaded, default event on screen)\n");

    // ——— boot: the default event, and a linkable URL ———
    const events = await page.evaluate(() => globalThis.__latentSky.events);
    check(
      events.length === 2 && events[0].id === HERO_EVENT.id && events[1].id === GLOBAL_EVENT.id,
      "catalogue offers both events in file order",
      JSON.stringify(events.map((e) => e.id)),
    );
    check(
      events.every((e) => typeof e.subtitle === "string" && e.subtitle.length > 0),
      "every event carries a subtitle for the switcher's secondary line",
      JSON.stringify(events.map((e) => e.subtitle)),
    );
    const switchers = await page.locator(".switcher").count();
    check(switchers === 1, "switcher is visible with two events", `${switchers} .switcher element(s)`);
    check(
      new URL(page.url()).searchParams.get("event") === HERO_EVENT.id,
      "boot writes ?event= for the default event (the link is shareable from first paint)",
      page.url(),
    );

    const heroState = await readState(page);
    checkEventLoaded(check, heroState, HERO_EVENT, "boot");
    check(
      heroState.trigger === HERO_EVENT.title,
      `boot: the switcher names the active event`,
      JSON.stringify(heroState.trigger),
    );
    const heroPixels = await sampleGrid(page);

    // ——— the switch, driven entirely from the keyboard ———
    await page.focus(".switcher .trigger");
    await page.keyboard.press("Enter");
    await page.waitForSelector("[role=listbox] [role=option]", { timeout: 5_000 });
    const listbox = await page.evaluate(() => {
      const options = [...document.querySelectorAll("[role=listbox] [role=option]")];
      return {
        count: options.length,
        expanded: document.querySelector(".switcher .trigger")?.getAttribute("aria-expanded"),
        selected: options.map((o) => o.getAttribute("aria-selected")),
        titles: options.map((o) => o.querySelector(".option-title")?.textContent?.trim() ?? null),
        subtitles: options.map((o) => o.querySelector(".option-subtitle")?.textContent?.trim() ?? null),
      };
    });
    check(
      listbox.count === 2 && listbox.expanded === "true",
      "Enter on the trigger opens a two-option listbox",
      `count=${listbox.count} aria-expanded=${listbox.expanded}`,
    );
    check(
      listbox.selected.join(",") === "true,false",
      "the active event is the one marked aria-selected",
      listbox.selected.join(","),
    );
    check(
      listbox.titles[0] === HERO_EVENT.title &&
        listbox.titles[1] === GLOBAL_EVENT.title &&
        listbox.subtitles.every((s) => typeof s === "string" && s.length > 0),
      "options show title with subtitle as secondary text",
      JSON.stringify(listbox.titles),
    );
    // Focus must be INSIDE the list, or arrow keys do nothing for a keyboard user.
    await page.waitForFunction(
      () => document.activeElement?.getAttribute("role") === "option",
      null,
      { timeout: 5_000 },
    );
    const focusedFirst = await page.evaluate(
      () => document.activeElement?.getAttribute("aria-selected"),
    );
    check(focusedFirst === "true", "opening focuses the active option", `aria-selected=${focusedFirst}`);

    await page.keyboard.press("ArrowDown");
    await page.keyboard.press("Enter");
    await page.waitForFunction(
      (id) => globalThis.__latentSky.ready === true && globalThis.__latentSky.activeEventId === id,
      GLOBAL_EVENT.id,
      { timeout: 45_000 },
    );

    check(
      new URL(page.url()).searchParams.get("event") === GLOBAL_EVENT.id,
      "the switch rewrites ?event= (history.replaceState)",
      page.url(),
    );
    const globalState = await readState(page);
    checkEventLoaded(check, globalState, GLOBAL_EVENT, "after switch");
    check(
      globalState.trigger === GLOBAL_EVENT.title,
      "after switch: the switcher names the new active event",
      JSON.stringify(globalState.trigger),
    );
    check(
      typeof globalState.caveat === "string" &&
        globalState.caveat.length > 0 &&
        globalState.caveat !== heroState.caveat,
      "the caveat copy follows the new manifest",
      JSON.stringify(globalState.caveat?.slice(0, 60)),
    );
    check(
      globalState.variables.length > 0 && globalState.variables.includes("wind10m"),
      "variable selection reset to one the new event actually has",
      JSON.stringify(globalState.variables),
    );
    const changedOnSwitch = await changedFrom(page, heroPixels);
    check(
      changedOnSwitch > 5,
      "the globe re-rendered for the new event",
      `${changedOnSwitch}/${heroPixels.length} sample points changed`,
    );
    const globalPixels = await sampleGrid(page);

    // ——— and back again: the reverse direction must restore everything the
    // hero-free event took away (chiefly the 8,000 km camera floor) ———
    await page.evaluate((id) => globalThis.__latentSky.switchEvent(id), HERO_EVENT.id);
    await page.waitForFunction(
      (id) => globalThis.__latentSky.ready === true && globalThis.__latentSky.activeEventId === id,
      HERO_EVENT.id,
      { timeout: 45_000 },
    );
    const backState = await readState(page);
    checkEventLoaded(check, backState, HERO_EVENT, "switched back");
    const changedBack = await changedFrom(page, globalPixels);
    check(
      changedBack > 5,
      "switching back re-rendered the globe again",
      `${changedBack}/${globalPixels.length} sample points changed`,
    );

    // ——— the nastiest disposal case: switch while a camera flight is in the
    // air. The outgoing director must cancel its own tween, or it keeps
    // advancing the shared camera towards a hero rectangle the new event does
    // not have — a flight to nowhere, with no error to show for it. ———
    await page.evaluate(
      (id) => {
        globalThis.__latentSky.enterStorm(); // starts the ~4.5 s fly-down
        return globalThis.__latentSky.switchEvent(id); // ...and switch out from under it
      },
      GLOBAL_EVENT.id,
    );
    await page.waitForFunction(
      (id) => globalThis.__latentSky.ready === true && globalThis.__latentSky.activeEventId === id,
      GLOBAL_EVENT.id,
      { timeout: 45_000 },
    );
    // Give any surviving tween a generous window to betray itself.
    await new Promise((r) => setTimeout(r, 1_500));
    const midFlight = await page.evaluate(() => {
      const view = globalThis.__latentSky.getView();
      const widget = document.querySelector(".globe")?.cesiumWidget;
      return { ...view, height: widget?.camera.positionCartographic.height ?? null };
    });
    check(
      midFlight.view === "orbit" && midFlight.flying === false,
      "switching mid-flight lands in the new event's orbit state",
      `view=${midFlight.view} flying=${midFlight.flying}`,
    );
    check(
      midFlight.height !== null && midFlight.height > 15_000_000,
      "the cancelled fly-down left the camera in orbit, not part-way down",
      `height=${midFlight.height === null ? "null" : (midFlight.height / 1000).toFixed(0) + " km"}`,
    );
    const midState = await readState(page);
    checkEventLoaded(check, midState, GLOBAL_EVENT, "after mid-flight switch");

    checkClean(check, diag);
  } catch (err) {
    console.error(`\n[${name}] SUITE ABORTED:`, err);
    if (diag.consoleErrors.length) console.error("console errors:", diag.consoleErrors);
    if (diag.pageErrors.length) console.error("page errors:", diag.pageErrors);
    if (diag.badResponses.length) console.error("bad responses:", diag.badResponses);
    failures.push(`[${name}] suite aborted: ${err}`);
  } finally {
    await page.close();
  }
}

/**
 * ?event= must open that event directly — the "send a colleague a link" case —
 * and an ?event= the catalogue does not carry must degrade to the default.
 */
async function runDeepLink() {
  const name = "catalogue-deep-link";
  console.log(`\n=== scenario: ${name} (?event=${GLOBAL_EVENT.id}) ===`);
  const check = checker(name);
  const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
  const diag = attachDiagnostics(page);
  try {
    await page.goto(
      `http://localhost:${PORT}/?catalogue=${CATALOGUE}&event=${GLOBAL_EVENT.id}&test=1`,
      { waitUntil: "load" },
    );
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 45_000 });
    const state = await readState(page);
    checkEventLoaded(check, state, GLOBAL_EVENT, "deep link");

    // A stale shared link — an ?event= the catalogue no longer carries — must
    // open the default event and correct the address bar, not show nothing.
    await page.goto(
      `http://localhost:${PORT}/?catalogue=${CATALOGUE}&event=no-such-event&test=1`,
      { waitUntil: "load" },
    );
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 45_000 });
    const stale = await readState(page);
    check(
      stale.activeEventId === HERO_EVENT.id,
      "an unknown ?event= falls back to the default event",
      `activeEventId=${stale.activeEventId}`,
    );
    check(
      new URL(page.url()).searchParams.get("event") === HERO_EVENT.id,
      "and the address bar is corrected to what is on screen",
      page.url(),
    );

    checkClean(check, diag);
  } catch (err) {
    console.error(`\n[${name}] SUITE ABORTED:`, err);
    if (diag.consoleErrors.length) console.error("console errors:", diag.consoleErrors);
    if (diag.pageErrors.length) console.error("page errors:", diag.pageErrors);
    failures.push(`[${name}] suite aborted: ${err}`);
  } finally {
    await page.close();
  }
}

/** One event is not a choice: the control must not exist at all. */
async function runSingleEventCatalogue() {
  const name = "catalogue-single";
  console.log(`\n=== scenario: ${name} (${CATALOGUE_SINGLE}) ===`);
  const check = checker(name);
  const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
  const diag = attachDiagnostics(page);
  try {
    await page.goto(`http://localhost:${PORT}/?catalogue=${CATALOGUE_SINGLE}&test=1`, {
      waitUntil: "load",
    });
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 45_000 });
    const switchers = await page.locator(".switcher").count();
    check(switchers === 0, "one-event catalogue renders NO switcher", `${switchers} .switcher element(s)`);
    check(
      new URL(page.url()).searchParams.has("event") === false,
      "no ?event= written when there is nothing to choose between",
      page.url(),
    );
    const state = await readState(page);
    check(
      state.activeEventId === GLOBAL_EVENT.id && state.frameCount === GLOBAL_EVENT.frameCount,
      "the single event still loads through the catalogue",
      `activeEventId=${state.activeEventId} frameCount=${state.frameCount}`,
    );
    checkClean(check, diag);
  } catch (err) {
    console.error(`\n[${name}] SUITE ABORTED:`, err);
    if (diag.consoleErrors.length) console.error("console errors:", diag.consoleErrors);
    if (diag.pageErrors.length) console.error("page errors:", diag.pageErrors);
    failures.push(`[${name}] suite aborted: ${err}`);
  } finally {
    await page.close();
  }
}

/**
 * The load-bearing degradation: NO catalogue at all. The site must fall back to
 * /data/web/manifest.json — which is exactly how it is deployed today — rather
 * than showing a dead globe. The catalogue's own 404 is the only >= 400 the
 * scenario tolerates, and it must not produce a console ERROR (an absent
 * catalogue is expected, not a defect).
 *
 * Run against BOTH shapes an absent file takes:
 *   /data/…      a real HTTP 404 from the dev data middleware
 *   /dev-fixture/… HTTP 200 serving index.html, because Vite (and any SPA-style
 *                CDN error mapping) answers unknown paths with the app shell —
 *                the same trap Architecture.md §10 records for Cesium assets.
 */
async function runMissingCatalogue(name, missing) {
  console.log(`\n=== scenario: ${name} (${missing}) ===`);
  const check = checker(name);
  const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
  const diag = attachDiagnostics(page);
  const warnings = [];
  page.on("console", (m) => {
    if (m.type() === "warning") warnings.push(m.text());
  });
  try {
    await page.goto(`http://localhost:${PORT}/?catalogue=${missing}&test=1`, { waitUntil: "load" });
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });

    const fellBack = diag.allRequests.some((u) => u.endsWith("/data/web/manifest.json"));
    check(fellBack, "fell back to the shipped /data/web/manifest.json", `requests=${diag.allRequests.length}`);
    const switchers = await page.locator(".switcher").count();
    check(switchers === 0, "no switcher without a catalogue", `${switchers} .switcher element(s)`);
    const state = await readState(page);
    check(
      state.activeEventId === null && state.frameCount >= 1,
      "the app runs catalogue-less with a live manifest",
      `activeEventId=${state.activeEventId} frameCount=${state.frameCount}`,
    );
    check(
      warnings.some((w) => w.includes("no catalogue at")),
      "the fallback is logged clearly",
      warnings.slice(0, 2).join(" | "),
    );
    const unexpected = diag.badResponses.filter((r) => !r.includes(missing));
    check(unexpected.length === 0, "the missing catalogue is the ONLY >= 400", unexpected.slice(0, 3).join(", "));
    // Chrome logs its own "Failed to load resource: …404" for any failed fetch,
    // with no URL in the message. Since the check above proves the catalogue is
    // the only failing response, that line can only be about the catalogue —
    // it is the browser narrating a 404, not the app reporting a defect.
    const appErrors = diag.consoleErrors.filter((e) => !/Failed to load resource/.test(e));
    check(
      appErrors.length === 0,
      "an absent catalogue is not an application console error",
      appErrors.slice(0, 3).join(" | "),
    );
    check(diag.pageErrors.length === 0, "zero page errors", diag.pageErrors.slice(0, 3).join(" | "));
  } catch (err) {
    console.error(`\n[${name}] SUITE ABORTED:`, err);
    if (diag.consoleErrors.length) console.error("console errors:", diag.consoleErrors);
    if (diag.pageErrors.length) console.error("page errors:", diag.pageErrors);
    failures.push(`[${name}] suite aborted: ${err}`);
  } finally {
    await page.close();
  }
}

/**
 * Missing weather. The manifest is valid and every frame is absent. Ready must
 * NOT be reported and the failure must be on screen: until 6 Sep 2026 the app
 * took Cesium's empty tile queue as ready, so a run whose frames all 404ed (or
 * arrived as the app shell with a 200) showed a dark planet under an intact
 * weather legend and said nothing. The legend now sits beside a visible error,
 * which is an honest state; a legend alone was not.
 */
async function runMissingFrames(name, manifest, expectCause) {
  console.log(`\n=== scenario: ${name} (${manifest}) ===`);
  const check = checker(name);
  const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
  const diag = attachDiagnostics(page);
  try {
    await page.goto(`http://localhost:${PORT}/?manifest=${manifest}&test=1`, { waitUntil: "load" });
    const panel = page.locator(".boot-error");
    await panel.waitFor({ state: "visible", timeout: 60_000 });
    const text = (await panel.textContent()) ?? "";
    check(expectCause.test(text), "the failure is on screen and names its cause", text.slice(0, 140));
    const ready = await page.evaluate(() => globalThis.__latentSky?.ready === true);
    check(!ready, "the app does NOT report ready without its weather", `ready=${ready}`);
  } catch (err) {
    console.error(`\n[${name}] SUITE ABORTED:`, err);
    if (diag.consoleErrors.length) console.error("console errors:", diag.consoleErrors.slice(0, 5));
    failures.push(`[${name}] suite aborted: ${err}`);
  } finally {
    await page.close();
  }
}

/**
 * The stale legend. The wind LUT is held in flight while the user switches to
 * water vapour; when the wind LUT finally arrives it must draw NOTHING. Until
 * 6 Sep 2026 it painted wind's ramp under water vapour's label. A fresh browser
 * context, because the earlier scenarios have the wind LUT in the shared cache
 * and a cached image is never requested, so the hold would never engage.
 */
async function runStaleLegend() {
  const name = "stale-legend";
  console.log(`\n=== scenario: ${name} ===`);
  const check = checker(name);
  const context = await browser.newContext({ viewport: { width: 1100, height: 750 } });
  const page = await context.newPage();
  let releaseWind = null;
  await page.route(/luts\/wind10m\.lut\.png/, (route) => {
    releaseWind = () => route.continue();
  });
  try {
    await page.goto(`http://localhost:${PORT}/?manifest=/dev-fixture/manifest.json&test=1`, { waitUntil: "load" });
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });
    check(releaseWind !== null, "the opening variable's LUT request was intercepted and is being held");
    await page.evaluate(() => globalThis.__latentSky.setVariable("tcwv"));
    await page.waitForTimeout(1500); // water vapour's LUT (not held) decodes and draws
    if (releaseWind) releaseWind(); // the stale wind LUT arrives AFTER the switch
    await page.waitForTimeout(1500);
    const [legend, tcwv, wind] = await page.evaluate(async () => {
      const c = document.querySelector(".legend canvas");
      if (!c) throw new Error("no legend canvas");
      const at = (ctx, w, h) => [...ctx.getImageData(w - 2, Math.floor(h / 2), 1, 1).data].slice(0, 3);
      const paint = async (url) => {
        const img = new Image();
        img.src = url;
        await img.decode();
        const t = document.createElement("canvas");
        t.width = c.width;
        t.height = c.height;
        const ctx = t.getContext("2d");
        ctx.imageSmoothingEnabled = false;
        ctx.fillStyle = "#070c1a";
        ctx.fillRect(0, 0, t.width, t.height);
        ctx.drawImage(img, 0, 0, t.width, t.height);
        return at(ctx, t.width, t.height);
      };
      return [
        at(c.getContext("2d"), c.width, c.height),
        await paint("/dev-fixture/luts/tcwv.lut.png"),
        await paint("/dev-fixture/luts/wind10m.lut.png"),
      ];
    });
    const dist = (a, b) => Math.max(...a.map((v, i) => Math.abs(v - b[i])));
    check(dist(tcwv, wind) > 8, "the two ramps differ at the sampled pixel, so the check can tell them apart", `tcwv=${tcwv} wind=${wind}`);
    check(dist(legend, tcwv) <= 8, "the legend shows the CURRENT variable's ramp after the stale LUT resolved", `legend=${legend} tcwv=${tcwv} wind=${wind}`);
  } catch (err) {
    console.error(`\n[${name}] SUITE ABORTED:`, err);
    failures.push(`[${name}] suite aborted: ${err}`);
  } finally {
    await context.close();
  }
}

/**
 * An archived run, linked from the verification record after the catalogue has
 * rolled it off. Until 12 Sep 2026 this opened the NEWEST run instead, rewrote
 * the address bar to match, and said nothing — a reader following a link to the
 * run that scored 0.086 was shown a different run's numbers. The run's data
 * never moves, so it must open as itself.
 */
async function runArchivedEvent() {
  const name = "archived-event";
  console.log(`\n=== scenario: ${name} (?event= a run the catalogue rolled off) ===`);
  const check = checker(name);
  const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
  const diag = attachDiagnostics(page);
  try {
    await page.goto(
      `http://localhost:${PORT}/?catalogue=/dev-fixture/catalogue-rolled-off.json&event=daily-2026-09-02&test=1`,
      { waitUntil: "load" },
    );
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });
    const s = await page.evaluate(() => ({
      active: globalThis.__latentSky.activeEventId,
      url: location.search,
      events: (globalThis.__latentSky.events || []).map((e) => e.id),
      trigger: document.querySelector(".trigger-title")?.textContent?.trim() ?? "",
      masthead: document.querySelector(".runid")?.textContent.replace(/\s+/g, " ").trim() ?? "",
      verification: document.querySelector(".verification")?.textContent.replace(/\s+/g, " ").trim() ?? "",
    }));
    check(s.active === "daily-2026-09-02", "the archived run is what opened", `active=${s.active}`);
    check(s.url.includes("event=daily-2026-09-02"), "the address bar still names it", s.url);
    check(s.events.includes("daily-2026-09-02"), "and it is in the switcher, so the reader can navigate", s.events.join(","));
    check(/2 Sep(t)? 2026/.test(s.trigger), "the switcher labels it by its own date", s.trigger);
    check(/02 Sept 2026, 12:00 UTC/.test(s.masthead), "the masthead is the archived run's own", s.masthead);
    check(/Scored against MRMS radar/.test(s.verification), "and its scored state is its own", s.verification.slice(0, 70));
    check(diag.pageErrors.length === 0, "zero page errors", diag.pageErrors.slice(0, 2).join(" | "));
  } catch (err) {
    console.error(`\n[${name}] SUITE ABORTED:`, err);
    if (diag.consoleErrors.length) console.error("console errors:", diag.consoleErrors.slice(0, 5));
    failures.push(`[${name}] suite aborted: ${err}`);
  } finally {
    await page.close();
  }
}

/** A genuinely unknown id is the visitor's typo, and still opens the default. */
async function runUnknownEvent() {
  const name = "unknown-event";
  console.log(`\n=== scenario: ${name} (?event= nothing that exists anywhere) ===`);
  const check = checker(name);
  const page = await browser.newPage({ viewport: { width: 1100, height: 750 } });
  try {
    await page.goto(
      `http://localhost:${PORT}/?catalogue=/dev-fixture/catalogue-rolled-off.json&event=daily-1999-01-01&test=1`,
      { waitUntil: "load" },
    );
    await page.waitForFunction(() => globalThis.__latentSky?.ready === true, null, { timeout: 60_000 });
    const active = await page.evaluate(() => globalThis.__latentSky.activeEventId);
    check(active === "synthetic-hero", "a nonexistent run still opens the default", `active=${active}`);
  } catch (err) {
    console.error(`\n[${name}] SUITE ABORTED:`, err);
    failures.push(`[${name}] suite aborted: ${err}`);
  } finally {
    await page.close();
  }
}

let exitCode = 1;
try {
  for (const fixture of FIXTURES) {
    await runFixture(fixture);
  }
  await runCatalogue();
  await runDeepLink();
  await runSingleEventCatalogue();
  await runMissingCatalogue("catalogue-404", "/data/web/no-such-catalogue.json");
  await runMissingCatalogue("catalogue-spa-fallback", "/dev-fixture/no-such-catalogue.json");
  await runMissingFrames("frames-404", "/dev-fixture/manifest-missing-404.json", /frame fetch failed: 404/);
  await runMissingFrames("frames-spa-fallback", "/dev-fixture/manifest-missing-spa.json", /decod/i);
  await runStaleLegend();
  await runArchivedEvent();
  await runUnknownEvent();
  exitCode = failures.length === 0 ? 0 : 1;
  console.log(
    failures.length === 0
      ? `\nSMOKE TEST PASSED (${FIXTURES.length} manifest fixtures + 10 scenarios)`
      : `\nSMOKE TEST FAILED: ${failures.length} check(s): ${failures.join("; ")}`,
  );
} finally {
  await browser.close();
  await server.close();
}
process.exit(exitCode);
