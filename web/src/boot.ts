/**
 * Post-probe bootstrap: work out WHICH event to open, fetch and validate its
 * manifest, seed the store, mount the Svelte tree. Loaded dynamically by
 * main.ts only after WebGL2 is proven.
 *
 * Three routes in, in strict precedence:
 *
 *   1. ?manifest=<url>  — explicit single manifest. Bypasses the catalogue
 *      entirely: no switcher, no ?event=. This is what the dev fixtures and the
 *      capture scripts drive, and it is exactly the behaviour that shipped.
 *   2. the catalogue at ?catalogue=<url> (or /data/web/catalogue.json) — the
 *      normal route once more than one event exists. ?event=<id> selects, the
 *      entry flagged `default` decides otherwise.
 *   2b. ?event=<id> naming a run the catalogue has rolled off — recovered from
 *      its permanent manifest address and added to the switcher, because the
 *      verification record links every run ever scored and those links must
 *      open the run they name.
 *   3. no usable catalogue — fall back to the single /data/web/manifest.json.
 *      loadCatalogue() has already logged why. THE SITE MUST NEVER BREAK
 *      BECAUSE A CATALOGUE IS MISSING: at time of writing the deployed site
 *      has no catalogue at all.
 */
import { mount } from "svelte";
import App from "./ui/App.svelte";
import {
  archivedDailyManifestUrl,
  archivedEvent,
  catalogueUrlFromLocation,
  chooseEvent,
  eventIdFromLocation,
  loadCatalogue,
  writeEventToUrl,
} from "./data/catalogue";
import { DEFAULT_MANIFEST_URL, explicitManifestUrl, loadManifest } from "./data/manifest";
import { sky } from "./state/store.svelte";

export async function boot(target: HTMLElement): Promise<void> {
  const explicit = explicitManifestUrl();

  if (explicit !== null) {
    sky.setCatalogue(null, null);
    sky.init(await loadManifest(explicit)); // throws loudly on any contract violation
    mount(App, { target });
    return;
  }

  const catalogue = await loadCatalogue(catalogueUrlFromLocation());

  if (catalogue === null) {
    sky.setCatalogue(null, null);
    sky.init(await loadManifest(DEFAULT_MANIFEST_URL));
    mount(App, { target });
    return;
  }

  const requested = eventIdFromLocation();
  let event = chooseEvent(catalogue, requested);

  // 2b. A run the catalogue no longer carries. The catalogue is a rolling
  //     window; the verification record links every run ever scored, and the
  //     run's data stays in the bucket for good. Without this, following the
  //     record's link to an older scored run opened the NEWEST run instead,
  //     silently, and rewrote the address bar to match (seen live, 12 Sep
  //     2026). Recover it from its own manifest and add it to the switcher, so
  //     the link opens the run it names and the reader can still navigate.
  if (requested !== null && event.id !== requested) {
    const url = archivedDailyManifestUrl(requested, new URL(window.location.href));
    if (url !== null) {
      const archived = await loadManifest(url).catch((err: unknown) => {
        // Not in the archive either: a mistyped or retired id, which is the
        // visitor's input rather than a fault here. Say so and open the
        // default, as before.
        console.warn(`[latent-sky] ?event=${requested} is not in the archive at ${url} either:`, err);
        return null;
      });
      if (archived !== null) {
        const restored = archivedEvent(requested, url, {
          // run.init is optional in the schema; the first frame is the same
          // instant, and it is the fallback lambda_publish.daily_entry uses.
          init: archived.run.init ?? archived.frameIso[0],
          stormName: archived.run.stormName,
          verification: archived.run.verification,
          hasHero: [...archived.layers.values()].some((l) => l.kind === "hero-fine"),
        });
        sky.setCatalogue({ ...catalogue, events: [...catalogue.events, restored] }, restored.id);
        sky.init(archived);
        mount(App, { target });
        return;
      }
      event = chooseEvent(catalogue, null);
    }
  }

  sky.setCatalogue(catalogue, event.id);
  sky.init(await loadManifest(event.manifestUrl));
  // Normalise the address bar to what is actually on screen, so the URL is a
  // link to this event from the first paint.
  writeEventToUrl(catalogue, event.id);
  mount(App, { target });
}
