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


def build_manifest(
    run: dict,
    frame_times: list[str],
    layers: list[LayerRecord],
    specs: dict[str, RampSpec],
) -> dict:
    """Assemble the manifest dict and run every pre-schema gate. Raises on any failure."""
    if not layers:
        raise ManifestError("no layers — refusing to emit an empty manifest")

    # Gates 1 and 2 — §7.2(b). These raise RampIdentityError / GlobalRangeError.
    verify_identity(layers)
    verify_global_ranges(layers, specs)

    # Gate 3 — frame bookkeeping.
    if any(a >= b for a, b in zip(frame_times, frame_times[1:])):
        raise ManifestError(f"frame times must be strictly increasing, got {frame_times}")
    for layer in layers:
        if len(layer.frames) != len(frame_times):
            raise ManifestError(
                f"layer {layer.layer_id}: {len(layer.frames)} frames for "
                f"{len(frame_times)} frame times"
            )

    layer_ids = {layer.layer_id for layer in layers}
    for layer in layers:
        if layer.pair_with is not None and layer.pair_with not in layer_ids:
            raise ManifestError(
                f"layer {layer.layer_id}: pairWith {layer.pair_with!r} is not a layer id"
            )

    manifest: dict = {
        "schemaVersion": 1,
        "run": run,
        "frames": list(frame_times),
        "layers": {},
    }
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
