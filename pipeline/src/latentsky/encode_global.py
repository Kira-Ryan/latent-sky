"""Encode the global ERA5 layers + dark basemap and emit the COMBINED manifest.

Consumes the raw fetch cached by `python -m latentsky.fetch_era5` (real ERA5,
anonymous ARCO, final not ERA5T) and the hero manifest already emitted by
`python -m latentsky.encode_dev`, and produces ONE manifest carrying both.

Layers (§8 rows 10-12, 720x361 = 0.5 deg):
  * wind10m-global  hypot(u10m, v10m) — ramp/vmin/vmax IDENTICAL to the hero wind
                    layers (0-55 m/s), so the per-variable identity gate passes.
  * t2m-global      the SAME 233.15-323.15 K range as the hero t2m. §7.1: the
                    identity gate is per-variable, so the clipped-legend option is
                    the one the pipeline permits — the label carries the honesty
                    note "clipped below −40 °C" (5.5% of the planet sits below the
                    floor in Antarctic winter; it saturates at index 0).
  * tcwv-global     davos LUT, 0-70 kg/m².

Geometry (§8): ERA5 longitudes are 0..360 -> rolled to −180..180; ARCO latitude
already runs 90..−90 north->south down the image; 1440x721 -> 720x361 by ::2
slicing — an exact subgrid (both poles and the prime meridian retained), never
averaged. rect [−180,−90,180,90]. (The samples are grid-registered — centres at
−180, −179.5, ... — which under the declared rect is a quarter-degree
registration offset, accepted at global zoom; §8 fixes 720x361 + this rect.)

Identity gates run for real: the hero layers' identity checksums are taken
VERBATIM from the existing manifest while the global layers' are recomputed from
the vendored LUT bytes + ramps.yaml — so if either has drifted since the hero
encode, verify_identity fails loudly before a manifest can exist.

Determinism: every input is a cached file and every encoder parameter is fixed;
re-running must reproduce the tree byte-for-byte. Per-tree sha256 is printed for
each emitted subtree so a re-run can be compared at a glance.

Usage:
    python -m latentsky.encode_global --raw data/dev/raw --out data/dev/encoded
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import time

import numpy as np

from . import basemap as basemap_mod
from . import budget, regrid
from .encode import LayerRecord, encode_frame, load_lut, make_layer_record
from .manifest import build_manifest, write_manifest
from .ramps import DEFAULT_CONFIG, DEFAULT_LUT_DIR, load_ramps

ERA5_SHAPE = (721, 1440)  # rows (lat 90..-90 x 0.25), cols (lon 0..359.75)

# §8 rows 10-12: the global layers' one true geometry.
GLOBAL_GRID = regrid.TargetGrid(
    west=-180.0, south=-90.0, east=180.0, north=90.0, width=720, height=361
)

# Idempotently appended to run.generatedNote — Copernicus wording per §12, verbatim.
ERA5_NOTE = (
    "The global layers are ERA5 reanalysis at 0.5°, fetched anonymously from the "
    "ARCO mirror — final ERA5, not ERA5T (§3.6). Contains modified Copernicus "
    "Climate Change Service information 2021."
)

BASEMAP_REL = "basemap/global-dark.webp"

# (layer_id, variable, label suffix appended to the ramps.yaml label)
GLOBAL_PLAN = [
    ("wind10m-global", "wind10m", " — ERA5 analysis, 0.5°"),
    ("t2m-global", "t2m", " — clipped below −40 °C"),
    ("tcwv-global", "tcwv", " — ERA5 analysis, 0.5°"),
]


class EncodeGlobalError(RuntimeError):
    """The global encode cannot proceed. Nothing partial survives to a manifest."""


# ------------------------------------------------------------------ geometry

def roll_to_180(field: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Reorder the ascending 0..360 longitude axis to −180..180. Exact reindex —
    no interpolation, no wrap column duplication. Returns (field, lon) rolled."""
    lon = np.asarray(lon, dtype=np.float64)
    if lon.ndim != 1 or field.shape[-1] != lon.size:
        raise ValueError(f"field last axis {field.shape} does not match lon {lon.shape}")
    if not (np.all(np.diff(lon) > 0) and lon[0] == 0.0 and lon[-1] < 360.0):
        raise ValueError(f"expected ascending 0..360 longitudes, got {lon[0]}..{lon[-1]}")
    split = int(np.searchsorted(lon, 180.0))
    if split == lon.size or lon[split] != 180.0:
        raise ValueError("longitude grid has no exact 180.0 column — cannot roll exactly")
    rolled_lon = np.concatenate([lon[split:] - 360.0, lon[:split]])
    rolled = np.concatenate([field[..., split:], field[..., :split]], axis=-1)
    return rolled, rolled_lon


def downsample_half(field: np.ndarray) -> np.ndarray:
    """1440x721 -> 720x361 by ::2 slicing — the exact 0.5 deg subgrid (§8), keeping
    row 0 (90 N), the last row (90 S) and every even column. Never averaged:
    averaging would blur exactly the gradients WebP must carry."""
    if field.shape[-2:] != ERA5_SHAPE:
        raise ValueError(f"expected trailing {ERA5_SHAPE}, got {field.shape}")
    return field[..., ::2, ::2]


def prepare_global(field: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """One ERA5 field (721x1440, lat 90..−90, lon 0..360) -> the 361x720 image
    array: north at row 0, −180..180 west->east, 0.5 deg."""
    if field.shape != ERA5_SHAPE:
        raise ValueError(f"expected {ERA5_SHAPE}, got {field.shape}")
    lat = np.asarray(lat, dtype=np.float64)
    if lat.shape != (ERA5_SHAPE[0],) or lat[0] != 90.0 or lat[-1] != -90.0 or not np.all(np.diff(lat) < 0):
        raise ValueError("expected ARCO latitude descending 90..-90 (north -> south down the image)")
    rolled, _ = roll_to_180(field, lon)
    return downsample_half(rolled)


# ------------------------------------------------------------------ tree hashing

def tree_sha256(root: pathlib.Path) -> str:
    """sha256 over a directory tree: sorted relative POSIX paths + file bytes.
    Two runs that produce identical trees produce identical digests."""
    root = pathlib.Path(root)
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        h.update(path.relative_to(root).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()


# ------------------------------------------------------------------ hero manifest intake

def hero_records_from_manifest(manifest: dict) -> list[LayerRecord]:
    """Reconstruct the hero LayerRecords VERBATIM from an existing manifest —
    including their identity checksums, so the identity gate genuinely compares
    what the hero frames were encoded with against today's LUTs + ramps.yaml."""
    records = []
    for layer_id, entry in manifest["layers"].items():
        if not entry["kind"].startswith("hero-"):
            continue
        records.append(LayerRecord(
            layer_id=layer_id,
            kind=entry["kind"],
            variable=entry["variable"],
            label=entry["label"],
            units=entry["units"],
            rect=entry["rect"],
            size=entry["size"],
            lut=entry["lut"],
            vmin=entry["vmin"],
            vmax=entry["vmax"],
            identity=entry["identity"],
            frames=entry["frames"],
            pair_with=entry.get("pairWith"),
        ))
    return records


# ------------------------------------------------------------------ main encode

def encode_layers(
    raw_dir: pathlib.Path,
    out_dir: pathlib.Path,
    config: pathlib.Path,
    lut_dir: pathlib.Path,
    tiles_dir: pathlib.Path,
    rebake_basemap: bool = False,
) -> None:
    t0 = time.perf_counter()

    npz_path = raw_dir / "era5_global.npz"
    if not npz_path.is_file():
        raise EncodeGlobalError(
            f"{npz_path} missing — fetch first: python -m latentsky.fetch_era5 "
            f"--meta {raw_dir / 'dev_meta.json'} --out {npz_path}"
        )
    npz = np.load(npz_path)
    times = [str(t) for t in npz["times"]]
    lat, lon = npz["latitude"], npz["longitude"]

    meta = json.loads((raw_dir / "dev_meta.json").read_text(encoding="utf-8"))
    if times != meta["times"]:
        raise EncodeGlobalError(
            f"era5_global.npz times {times} != dev_meta.json times {meta['times']} — "
            "refetch with the dev timestamps"
        )

    manifest_path = out_dir / "manifest.json"
    if not manifest_path.is_file():
        raise EncodeGlobalError(
            f"{manifest_path} missing — encode the hero layers first: "
            f"python -m latentsky.encode_dev --raw {raw_dir} --out {out_dir}"
        )
    existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    hero_records = hero_records_from_manifest(existing)
    if not hero_records:
        raise EncodeGlobalError(f"{manifest_path} carries no hero layers — wrong manifest?")
    if existing["frames"] != times:
        raise EncodeGlobalError(
            f"existing manifest frames {existing['frames']} != ERA5 times {times}"
        )

    specs = load_ramps(config)

    # Vendor the LUTs the global layers use (wind10m/t2m are shared with the hero
    # layers and MUST be the same bytes; tcwv is new here).
    (out_dir / "luts").mkdir(parents=True, exist_ok=True)
    luts: dict[str, tuple[np.ndarray, str, str]] = {}
    for variable in {p[1] for p in GLOBAL_PLAN}:
        src = lut_dir / specs[variable].lut_filename
        if not src.is_file():
            raise FileNotFoundError(
                f"{src} missing — bake the committed LUTs first: python -m latentsky.ramps"
            )
        rel = f"luts/{specs[variable].lut_filename}"
        shutil.copyfile(src, out_dir / rel)
        lut, sha = load_lut(out_dir / rel)
        luts[variable] = (lut, sha, rel)

    # The three global fields, derived exactly as the hero side derives them
    # (§3.1: wind speed is hypot(u, v) BEFORE ramping, identically on both sides).
    fields = {
        "wind10m": np.hypot(npz["u10m"].astype(np.float64), npz["v10m"].astype(np.float64)),
        "t2m": npz["t2m"].astype(np.float64),
        "tcwv": npz["tcwv"].astype(np.float64),
    }
    t2m_below = float((fields["t2m"] < specs["t2m"].vmin).mean() * 100.0)
    print(f"era5 ranges: wind10m {fields['wind10m'].min():.2f}..{fields['wind10m'].max():.2f} m/s | "
          f"t2m {fields['t2m'].min():.2f}..{fields['t2m'].max():.2f} K "
          f"({t2m_below:.2f}% below the {specs['t2m'].vmin} K floor -> clipped, per the label) | "
          f"tcwv {fields['tcwv'].min():.2f}..{fields['tcwv'].max():.2f} kg/m2")

    global_records: list[LayerRecord] = []
    for layer_id, variable, suffix in GLOBAL_PLAN:
        spec = specs[variable]
        lut, lut_sha, lut_rel = luts[variable]
        stack = fields[variable]
        if stack.shape != (len(times), *ERA5_SHAPE):
            raise EncodeGlobalError(f"{variable}: expected {(len(times), *ERA5_SHAPE)}, got {stack.shape}")
        frames: list[str] = []
        total = 0
        for i in range(len(times)):
            rel = f"layers/{layer_id}/{i:03d}.webp"
            field = prepare_global(stack[i], lat, lon)
            total += encode_frame(field, lut, spec.vmin, spec.vmax, out_dir / rel)
            frames.append(rel)
        global_records.append(make_layer_record(
            layer_id=layer_id, kind="global", spec=spec, lut_sha256=lut_sha,
            lut_rel_path=lut_rel, rect=GLOBAL_GRID.rect, size=GLOBAL_GRID.size,
            frames=frames, label_suffix=suffix,
        ))
        print(f"  {layer_id:16s} global      {GLOBAL_GRID.width:4d}x{GLOBAL_GRID.height:<4d} "
              f"{len(frames)} frames  {total:>9,} B  ({total / len(frames):,.0f} B/frame)")

    # The dark basemap (public domain Natural Earth II -> shippable). Baked once;
    # --rebake-basemap forces a re-bake.
    basemap_path = out_dir / BASEMAP_REL
    if rebake_basemap or not basemap_path.is_file():
        size = basemap_mod.bake(tiles_dir, basemap_path)
        print(f"  basemap baked: {BASEMAP_REL}  {size:,} B  "
              f"({basemap_mod.WIDTH}x{basemap_mod.HEIGHT} WebP q{basemap_mod.QUALITY}, lossy — scenery, not data)")
    else:
        print(f"  basemap kept:  {BASEMAP_REL}  {basemap_path.stat().st_size:,} B  (already baked)")

    # Combined run note, idempotent so a re-run reproduces identical bytes.
    run = dict(existing["run"])
    if not run["generatedNote"].endswith(ERA5_NOTE):
        run["generatedNote"] = run["generatedNote"] + " " + ERA5_NOTE

    manifest = build_manifest(
        run, times, hero_records + global_records, specs,
        basemap={"global": BASEMAP_REL, "globalRect": GLOBAL_GRID.rect},
    )
    written = write_manifest(manifest, out_dir)
    print(f"\ncombined manifest validated against schema and written: {written}")
    print(f"  layers: {len(hero_records)} hero (verbatim) + {len(global_records)} global | "
          f"frames: {len(times)} | basemap: {BASEMAP_REL}")

    print("\nper-tree sha256:")
    for rel in [*(f"layers/{p[0]}" for p in GLOBAL_PLAN), "basemap", "luts"]:
        print(f"  {rel + '/':32s} {tree_sha256(out_dir / rel)}")
    print(f"  {'<entire output tree>':32s} {tree_sha256(out_dir)}")

    print(f"\nencode wall time: {time.perf_counter() - t0:.1f} s")
    _, within = budget.report(out_dir)
    if not within:
        raise SystemExit(1)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    ap.add_argument("--luts", type=pathlib.Path, default=DEFAULT_LUT_DIR)
    ap.add_argument("--tiles", type=pathlib.Path, default=basemap_mod.DEFAULT_TILES)
    ap.add_argument("--rebake-basemap", action="store_true")
    args = ap.parse_args(argv)
    encode_layers(args.raw, args.out, args.config, args.luts, args.tiles, args.rebake_basemap)


if __name__ == "__main__":
    main()
