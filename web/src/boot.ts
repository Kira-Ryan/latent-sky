/**
 * Post-probe bootstrap: fetch and validate the manifest, seed the store, mount
 * the Svelte tree. Loaded dynamically by main.ts only after WebGL2 is proven.
 */
import { mount } from "svelte";
import App from "./ui/App.svelte";
import { loadManifest, manifestUrlFromLocation } from "./data/manifest";
import { sky } from "./state/store.svelte";

export async function boot(target: HTMLElement): Promise<void> {
  const url = manifestUrlFromLocation();
  const manifest = await loadManifest(url); // throws loudly on any contract violation
  sky.init(manifest);
  mount(App, { target });
}
