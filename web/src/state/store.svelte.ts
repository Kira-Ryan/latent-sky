/**
 * Runes store — the single source of truth for UI state.
 * Components mutate this; App.svelte's $effects push changes into the globe
 * façade. Nothing in here imports Cesium (§6.4).
 */
import type { Catalogue, CatalogueEvent } from "../data/catalogue";
import {
  availableVariables,
  globalLayerFor,
  primaryLayerFor,
  type LayerDef,
  type Manifest,
  type Variable,
} from "../data/manifest";

class SkyStore {
  manifest = $state<Manifest | null>(null);

  /** The event index, or null when the site is running catalogue-less. */
  catalogue = $state<Catalogue | null>(null);
  /** id of the event currently on screen; null in single-manifest mode. */
  activeEventId = $state<string | null>(null);
  /** True for the duration of an event switch — the globe is being re-initialised. */
  switching = $state(false);

  variable = $state<Variable>("wind10m");
  /** Fractional frame index in [0, frameCount - 1]. */
  frame = $state(0);
  playing = $state(false);
  speed = $state(1);
  /** Wipe divider position in [0, 1]. */
  split = $state(0.5);
  showFine = $state(true);

  /** Where the camera lives: in orbit (arrival state) or down at the hero. */
  view = $state<"orbit" | "hero">("orbit");
  /** True for the duration of a camera flight (either direction). */
  flying = $state(false);
  /** Set on arrival at the hero: the reveal plays ONE auto-sweep, then rests mid. */
  sweepPending = $state(false);
  /** The wipe is engaged only once the camera has settled at the hero (§8.4). */
  revealEngaged: boolean = $derived(this.view === "hero" && !this.flying);

  events: CatalogueEvent[] = $derived(this.catalogue?.events ?? []);
  activeEvent: CatalogueEvent | undefined = $derived(
    this.events.find((e) => e.id === this.activeEventId),
  );
  /**
   * The switcher exists only when there is a choice to make. One event — which
   * is today's state, and the state a catalogue-less deployment is in — renders
   * no control at all.
   */
  showSwitcher: boolean = $derived(this.events.length > 1);

  /**
   * Whether the LOADED MANIFEST ships a hero region to fly down to. Derived
   * from the layer set, never from the catalogue's `hasHero` hint: the manifest
   * is the authority, so a stale catalogue can never make the UI offer a way
   * down that the data cannot honour.
   */
  heroAvailable: boolean = $derived(
    this.manifest !== null &&
      [...this.manifest.layers.values()].some((l) => l.kind === "hero-fine"),
  );

  variables: Variable[] = $derived(this.manifest ? availableVariables(this.manifest) : []);
  frameCount: number = $derived(this.manifest?.frameIso.length ?? 0);
  activeLayer: LayerDef | undefined = $derived(
    this.manifest ? primaryLayerFor(this.manifest, this.variable) : undefined,
  );
  /**
   * The layer the legend should describe. From orbit the screen is dominated
   * by the global field, so its label (e.g. "ERA5 analysis, 0.5°", or t2m's
   * "clipped below −40 °C" note) is the honest one; the hero-fine label
   * applies only once the camera is down at the hero. Same LUT either way —
   * identity is enforced across layers — only the words change.
   */
  legendLayer: LayerDef | undefined = $derived.by(() => {
    if (!this.manifest) return undefined;
    if (this.view === "orbit") {
      return globalLayerFor(this.manifest, this.variable) ?? this.activeLayer;
    }
    return this.activeLayer;
  });
  /** The layer the active hero layer is paired with for the reveal, if any:
   *  a coarse model input, or an observed field (MRMS radar) the forecast is
   *  compared against. Both sit on the left of the wipe. */
  pairLayer: LayerDef | undefined = $derived.by(() => {
    const id = this.activeLayer?.pairWith;
    const p = id === undefined ? undefined : this.manifest?.layers.get(id);
    return p && (p.kind === "hero-coarse" || p.kind === "hero-observed") ? p : undefined;
  });
  /** Whether the active variable has a hero pair (the reveal). */
  hasPair: boolean = $derived(this.pairLayer !== undefined);
  /** aria-valuetext for the scrubber — always a valid time. */
  nearestFrameIso: string = $derived.by(() => {
    const m = this.manifest;
    if (!m) return "";
    const i = Math.min(Math.max(Math.round(this.frame), 0), m.frameIso.length - 1);
    return m.frameIso[i];
  });

  /** Record the event index. Display and routing only — never rendering. */
  setCatalogue(catalogue: Catalogue | null, activeEventId: string | null): void {
    this.catalogue = catalogue;
    this.activeEventId = activeEventId;
  }

  /**
   * Adopt a manifest — at boot, and again on every event switch.
   *
   * This is the FULL view reset, and it is deliberately exhaustive: every
   * mutable field below is set, not merely the ones a first boot happens to
   * need. Switching events must not carry one frame of the previous event's
   * state across (a variable absent from the new manifest, a frame index past
   * its end, a wipe left mid-sweep, a camera believing it is at a hero that no
   * longer exists), and the way to guarantee that is to reset here rather than
   * to remember which fields matter.
   *
   * The values chosen are exactly the ones globe/index.ts builds a fresh
   * session with, so store and renderer agree without a single setter call.
   */
  init(manifest: Manifest): void {
    const vars = availableVariables(manifest);
    if (vars.length === 0) throw new Error("manifest declares no renderable layers");

    this.manifest = manifest;
    this.variable = vars.includes("wind10m") ? "wind10m" : vars[0];
    this.frame = 0;
    this.playing = false;
    this.speed = 1;
    this.split = 0.5;
    this.showFine = true;
    this.view = "orbit";
    this.flying = false;
    this.sweepPending = false;
  }
}

export const sky = new SkyStore();
