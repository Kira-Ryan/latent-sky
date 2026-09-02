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
 *   3. no usable catalogue — fall back to the single /data/web/manifest.json.
 *      loadCatalogue() has already logged why. THE SITE MUST NEVER BREAK
 *      BECAUSE A CATALOGUE IS MISSING: at time of writing the deployed site
 *      has no catalogue at all.
 */
import { mount } from "svelte";
import App from "./ui/App.svelte";
import {
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

  const event = chooseEvent(catalogue, eventIdFromLocation());
  sky.setCatalogue(catalogue, event.id);
  sky.init(await loadManifest(event.manifestUrl));
  // Normalise the address bar to what is actually on screen, so the URL is a
  // link to this event from the first paint.
  writeEventToUrl(catalogue, event.id);
  mount(App, { target });
}
