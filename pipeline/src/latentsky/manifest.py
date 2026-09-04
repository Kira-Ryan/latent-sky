"""Emit manifest.json conforming EXACTLY to schema/manifest.schema.json — §10.

The manifest is THE contract between pipeline/ and web/: the web app renders
only what this file declares, and the pipeline emits it LAST, after every
referenced asset exists on disk and every identity gate has passed. Order of
operations here is therefore load-bearing:

    1. verify_identity()       — coarse/fine ramp identities equal per variable
    2. verify_global_ranges()  — every layer on the GLOBAL vmin/vmax
    3. frame times strictly increasing, frame arrays the right length
    4. every referenced frame file and LUT exists
    5. jsonschema Draft 2020-12 validation against the committed schema
    6. only then write manifest.json

Any failure raises; nothing is written on a failed gate.
"""

from __future__ import annotations

import json
import pathlib

import jsonschema

from .encode import LayerRecord, verify_global_ranges, verify_identity
from .ramps import RampSpec

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "schema" / "manifest.schema.json"


class ManifestError(RuntimeError):
    """The manifest could not be emitted. The build must stop."""


def load_schema(schema_path: pathlib.Path = SCHEMA_PATH) -> dict:
    with open(schema_path, "r", encoding="utf-8") as fh:
        return json.load(fh)


_BASEMAP_KEYS = {"global", "globalRect", "hero", "heroRect"}

# Config key (snake_case, as in configs/event_*.yaml) -> schema run field (camelCase).
_HINT_KEYS = {
    "storm_name": "stormName",
    "hero_frame": "heroFrame",
    "place_label": "placeLabel",
    "report": "reportUrl",
    "default_variable": "defaultVariable",
}


def run_hints(cfg: dict) -> dict:
    """Extract the OPTIONAL first-class run hints from a config dict.

    Config-driven by design: event configs (configs/event_*.yaml) and the dev
    encode both route their {storm_name, hero_frame, place_label} through here,
    so the UI never regex-parses a storm name out of generatedNote or guesses
    the hero frame. Missing keys are simply absent from the result — the web
    app falls back to its heuristics. Wrong types raise, loudly: a silently
    dropped hint would present as the heuristic, which is exactly the failure
    class this field exists to remove. hero_frame bounds against the frame
    count are gated later, in build_manifest, where the frames are known.
    """
    hints: dict = {}
    for key, field in _HINT_KEYS.items():
        if key not in cfg or cfg[key] is None:
            continue
        value = cfg[key]
        if key == "hero_frame":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ManifestError(
                    f"config {key}: expected a non-negative integer frame index, got {value!r}"
                )
        else:
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"config {key}: expected a non-empty string, got {value!r}")
        hints[field] = value
    return hints


def build_manifest(
    run: dict,
    frame_times: list[str],
    layers: list[LayerRecord],
    specs: dict[str, RampSpec],
    basemap: dict | None = None,
) -> dict:
    """Assemble the manifest dict and run every pre-schema gate. Raises on any failure.

    `basemap` is the optional schema "basemap" object ({global, globalRect, hero,
    heroRect}); its imagery must be public-domain or CC-BY (schema note) and its
    referenced files are existence-checked by write_manifest like every layer asset.
    """
    if not layers:
        raise ManifestError("no layers — refusing to emit an empty manifest")

    if basemap is not None:
        unknown = set(basemap) - _BASEMAP_KEYS
        if unknown:
            raise ManifestError(f"basemap carries unknown keys {sorted(unknown)}")
        if not ("global" in basemap or "hero" in basemap):
            raise ManifestError("basemap object declares no imagery at all")
        for key in ("globalRect", "heroRect"):
            if key in basemap:
                rect = basemap[key]
                if len(rect) != 4 or not all(isinstance(v, (int, float)) for v in rect):
                    raise ManifestError(f"basemap.{key} must be [west, south, east, north], got {rect!r}")

    # Gates 1 and 2 — §7.2(b). These raise RampIdentityError / GlobalRangeError.
    verify_identity(layers)
    verify_global_ranges(layers, specs)

    # Gate 3 — frame bookkeeping.
    if any(a >= b for a, b in zip(frame_times, frame_times[1:])):
        raise ManifestError(f"frame times must be strictly increasing, got {frame_times}")
    hero_frame = run.get("heroFrame")
    if hero_frame is not None:
        if isinstance(hero_frame, bool) or not isinstance(hero_frame, int):
            raise ManifestError(f"run.heroFrame must be an integer frame index, got {hero_frame!r}")
        if not 0 <= hero_frame < len(frame_times):
            raise ManifestError(
                f"run.heroFrame {hero_frame} is out of range for {len(frame_times)} frames — "
                "the schema cannot cross-reference frames, so this gate lives here"
            )
    for layer in layers:
        if len(layer.frames) != len(frame_times):
            raise ManifestError(
                f"layer {layer.layer_id}: {len(layer.frames)} frames for "
                f"{len(frame_times)} frame times"
            )

    # A declared default variable must exist as a renderable layer, or the UI
    # would silently fall back and the config's intent would vanish without a
    # word. Gated here because a JSON schema cannot cross-reference the layers.
    wanted = run.get("defaultVariable")
    if wanted is not None:
        available = {layer.variable for layer in layers if layer.kind in ("global", "hero-fine")}
        if wanted not in available:
            raise ManifestError(
                f"run.defaultVariable {wanted!r} has no global or hero-fine layer in this run "
                f"(emitted: {sorted(available)})"
            )

    by_id = {layer.layer_id: layer for layer in layers}
    for layer in layers:
        if layer.pair_with is None:
            continue
        if layer.pair_with not in by_id:
            raise ManifestError(
                f"layer {layer.layer_id}: pairWith {layer.pair_with!r} is not a layer id"
            )
        # The viewer refuses a pair whose variables differ (one legend cannot
        # describe two ramps), and it does so at load time in the browser. Fail
        # here instead, where the build can see it.
        other = by_id[layer.pair_with]
        if other.variable != layer.variable:
            raise ManifestError(
                f"layer {layer.layer_id} ({layer.variable}) pairs with {other.layer_id} "
                f"({other.variable}) — a reveal must compare the same variable"
            )

    manifest: dict = {
        "schemaVersion": 1,
        "run": run,
        "frames": list(frame_times),
    }
    if basemap is not None:
        manifest["basemap"] = {
            k: (list(map(float, v)) if k.endswith("Rect") else str(v))
            for k, v in basemap.items()
        }
    manifest["layers"] = {}
    for layer in layers:
        entry: dict = {
            "kind": layer.kind,
            "variable": layer.variable,
            "label": layer.label,
            "units": layer.units,
            "rect": [float(x) for x in layer.rect],
            "size": [int(x) for x in layer.size],
            "lut": layer.lut,
            "vmin": float(layer.vmin),
            "vmax": float(layer.vmax),
            "identity": layer.identity,
            "frames": list(layer.frames),
        }
        if layer.pair_with is not None:
            entry["pairWith"] = layer.pair_with
        if layer.native_km is not None:
            entry["nativeKm"] = layer.native_km
        manifest["layers"][layer.layer_id] = entry
    return manifest


def write_manifest(
    manifest: dict,
    out_dir: pathlib.Path,
    schema_path: pathlib.Path = SCHEMA_PATH,
) -> pathlib.Path:
    """Gate 4 (assets exist) + gate 5 (schema) and only then write manifest.json."""
    out_dir = pathlib.Path(out_dir)

    missing: list[str] = []
    for key in ("global", "hero"):
        rel = manifest.get("basemap", {}).get(key)
        if rel is not None and not (out_dir / rel).is_file():
            missing.append(f"basemap.{key}: {rel}")
    for layer_id, layer in manifest["layers"].items():
        for rel in [layer["lut"], *layer["frames"]]:
            if not (out_dir / rel).is_file():
                missing.append(f"{layer_id}: {rel}")
    if missing:
        raise ManifestError(
            "manifest references assets that do not exist — the manifest must be emitted "
            "last:\n  " + "\n  ".join(missing)
        )

    schema = load_schema(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.absolute_path))
    if errors:
        detail = "\n  ".join(
            f"at {'/'.join(str(p) for p in err.absolute_path) or '<root>'}: {err.message}"
            for err in errors
        )
        raise ManifestError(f"manifest fails schema validation:\n  {detail}")

    path = out_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path
