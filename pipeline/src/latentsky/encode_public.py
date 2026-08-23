"""Encode the PUBLISHABLE dataset into data/web — the committed, deployed artefact.

This is the pre-forecast public subset ONLY (Architecture.md §11, §12):

  * the three global ERA5 layers   wind10m / t2m / tcwv, 720x361, ERA5 is CC-BY
  * the dark basemap               Natural Earth II + 110m coastlines, public domain
  * the LUT PNGs                   baked from MIT colour maps
  * manifest.json                  run.kind "dev-sample", NO hero layers

v2 — a REAL weather sequence (live-site feedback: five unrelated monthly
snapshots made play/scrub read as meaningless morphing). The raw cache is the
week of Typhoon Gaemi: 32 6-hourly final-ERA5 analysis steps, 2024-07-22T00Z
through 2024-07-29T18Z, cached at the exact 0.5 deg subgrid by fetch_era5
--half-degree. The public encode ships every OTHER cached step — 16 12-hourly
frames for ALL THREE variables, because the manifest's frames array is shared
across layers (schema: one frames array; layers may not subsample it), and 16
frames of real motion beat a per-layer cadence split the app cannot express.
The 6-hourly cache keeps the wind-cadence upgrade a laptop re-encode away.

PUBLIC-CONTEXT RANGE OVERRIDE (t2m): the ramps.yaml floor of 233.15 K is the
hero-pair range (§7.1); on a whole planet it clips the entire Antarctic winter
plateau to index 0, which — with everything tropical saturating high — is what
made global t2m read as a uniform bright ball. The public manifest carries a
single t2m layer and no hero pair, so this tool widens the floor to 203.15 K
(−70 °C) via an explicit override of the loaded spec. The per-variable identity
gate (§7.2b) still passes because there is exactly one t2m layer here; when the
hero arrives, the gate must first learn pair-scoped identity (§7.1's note)
before a hero t2m can join this manifest. The LUT PNG is reused byte-identical:
it carries colours only (opaque alpha — asserted, because a ramp alpha policy
bakes vmin into the alpha channel); the range lives in the manifest vmin/vmax,
which is where the Legend reads it from.

The hero layers under data/dev derive from the CorrDiff package's CC BY-NC-ND
4.0 sample dataset and must NEVER appear in anything committed or deployed.
This tool therefore never reads cwb_sample.npz and enforces the boundary twice,
belt and braces (LICENCE GATE below):

  1. verify_publishable(): the build FAILS if any emitted layer kind is not in
     PUBLISHABLE_KINDS — a hero-* kind can never reach the manifest.
  2. verify_no_hero_bytes(): when data/dev/encoded exists locally, every emitted
     file is hashed against every dev hero frame and the build FAILS on any
     byte-identical match. tests/test_encode_public.py proves the same property
     over the committed data/web tree.

Determinism: every input is a cached file (era5_gaemi_week.npz), a committed
config (ramps.yaml), a committed LUT bake, the vendored Natural Earth II tiles,
or the cached 110m coastline, and every encoder parameter is fixed — a re-run
must reproduce the tree byte-for-byte. The managed output entries are replaced
wholesale on every run and the tool refuses to run over a directory carrying
anything else, so the printed whole-tree sha256 is honestly comparable between
runs.

Usage:
    python -m latentsky.encode_public --raw data/dev/raw --out data/web
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import shutil
import time

import numpy as np

from . import basemap as basemap_mod
from . import budget
from .encode import LayerRecord, encode_frame, load_lut, make_layer_record
from .encode_global import BASEMAP_REL, GLOBAL_GRID, roll_to_180, tree_sha256
from .fetch_era5 import HALF_SHAPE, times_from_range
from .manifest import build_manifest, write_manifest
from .ramps import DEFAULT_CONFIG, DEFAULT_LUT_DIR, RampSpec, load_ramps

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEV_ENCODED_DIR = REPO_ROOT / "data" / "dev" / "encoded"

# The ONLY layer kinds this tool may emit. hero-fine / hero-coarse derive from
# CC BY-NC-ND data until the first real forecast run and are non-publishable.
PUBLISHABLE_KINDS = frozenset({"global"})

# Everything encode_public owns inside --out. Replaced wholesale each run;
# anything else present is a loud error, never silently kept or deleted.
MANAGED_ENTRIES = ("layers", "luts", "basemap", "manifest.json")

# The Gaemi-week sequence — §3.6-gated final ERA5 (>3 months old, ARCO).
RAW_NAME = "era5_gaemi_week.npz"
SEQUENCE_START = "2024-07-22T00:00:00Z"
SEQUENCE_END = "2024-07-29T18:00:00Z"
CACHE_STEP_HOURS = 6            # what fetch_era5 caches (32 steps)
PUBLIC_STEP_HOURS = 12          # what ships (16 frames — see module docstring)
CACHE_TIMES = times_from_range(SEQUENCE_START, SEQUENCE_END, CACHE_STEP_HOURS)
PUBLIC_TIMES = [t for t in CACHE_TIMES if t[11:13] in ("00", "12")]

# PUBLIC-CONTEXT OVERRIDE — see module docstring. −70 °C floor for the global
# t2m layer; the LUT is reused byte-identical, only manifest vmin changes.
T2M_PUBLIC_VMIN = 203.15

# (layer_id, variable, label suffix appended to the ramps.yaml label).
# Public-specific — encode_global's dev-side plan keeps the hero-paired ranges.
PUBLIC_PLAN = [
    ("wind10m-global", "wind10m", " — ERA5 analysis, 0.5°"),
    ("t2m-global", "t2m", " — ERA5 analysis, 0.5°"),
    ("tcwv-global", "tcwv", " — ERA5 analysis, 0.5°"),
    ("msl-global", "msl", " — ERA5 analysis, 0.5°"),
]

# Public MSLP range: symmetric about the vik ramp's 1013.25 hPa midpoint (§7.1 —
# a diverging ramp about an off-centre midpoint lies about anomaly sign). Sized
# from the MEASURED Gaemi-week extremes (939.96..1050.11 hPa over all 32 fetched
# steps; the deep tail is Gaemi itself): the low side needs 73.29 hPa, so ±75.
# The high side wastes ~38 hPa of ramp — the price of an honest midpoint.
# Same LUT-reuse legality argument as t2m: opaque alpha => range-independent LUT.
MSL_PUBLIC_SPAN_HPA = 75.0

# The note gates below (the exact Gaemi-week cache, 12-hourly public frames)
# exist because this string asserts those facts — the caption and the data
# cannot drift apart.
GENERATED_NOTE = (
    "Global fields are ERA5 reanalysis — sixteen 12-hourly timesteps spanning "
    "the week of Typhoon Gaemi (22–29 July 2024). Reanalysis, not a forecast. "
    "The kilometre-scale AI-generated hero layer arrives with the first "
    "forecast run. Contains modified Copernicus Climate Change Service "
    "information 2024."
)

RUN = {
    "id": "public-era5-gaemi-week-2024",
    "kind": "dev-sample",
    "model": {
        "prognostic": "ERA5 reanalysis (ECMWF, anonymous ARCO mirror) — "
                      "analysis, not a model forecast",
        "downscaling": "none yet — the CorrDiffTaiwan ~2 km hero layer arrives "
                       "with the first forecast run",
    },
    "generatedNote": GENERATED_NOTE,
    # stormName / heroFrame / placeLabel deliberately absent: they drive the
    # hero experience, which does not exist in the pre-forecast public subset.
    # Gaemi is named in generatedNote as honest context for the sequence dates.
}


class EncodePublicError(RuntimeError):
    """The public encode cannot proceed. Nothing partial survives to a manifest."""


class PublicLicenceError(RuntimeError):
    """A non-publishable layer or byte sequence reached the public encode (§3.10)."""


# ------------------------------------------------------------------ licence gates

def verify_publishable(records: list[LayerRecord]) -> None:
    """LICENCE GATE 1 — §3.10 belt: every emitted layer kind must be publishable.

    hero-* layers derive from CC BY-NC-ND data until the first real forecast run;
    one reaching this tool is a licence violation, so the build stops dead.
    """
    bad = [(r.layer_id, r.kind) for r in records if r.kind not in PUBLISHABLE_KINDS]
    if bad:
        detail = ", ".join(f"{layer_id} (kind {kind!r})" for layer_id, kind in bad)
        raise PublicLicenceError(
            f"non-publishable layer kinds in the public encode: {detail}. "
            f"Only {sorted(PUBLISHABLE_KINDS)} may ship pre-forecast — hero-* layers "
            "derive from CC BY-NC-ND data and must never be committed or deployed."
        )


def dev_hero_frame_hashes(dev_encoded_dir: pathlib.Path) -> dict[str, str]:
    """sha256 -> description for every hero-* frame the LOCAL dev encode holds.

    Reads the dev manifest to find layers whose kind starts with "hero-";
    returns {} when the dev encode is absent (CI has no data/dev at all).
    """
    manifest_path = pathlib.Path(dev_encoded_dir) / "manifest.json"
    if not manifest_path.is_file():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    hashes: dict[str, str] = {}
    for layer_id, entry in manifest["layers"].items():
        if not entry["kind"].startswith("hero-"):
            continue
        for rel in entry["frames"]:
            frame = dev_encoded_dir / rel
            if not frame.is_file():
                raise EncodePublicError(
                    f"dev manifest references a missing hero frame: {frame} — "
                    "the byte-identity licence gate cannot run against a broken tree"
                )
            digest = hashlib.sha256(frame.read_bytes()).hexdigest()
            hashes[digest] = f"{layer_id}: {rel}"
    return hashes


def verify_no_hero_bytes(out_dir: pathlib.Path, dev_encoded_dir: pathlib.Path) -> int:
    """LICENCE GATE 2 — §3.10 braces: no emitted file may be byte-identical to any
    dev hero frame. Returns how many hero frames were compared against (0 when
    data/dev/encoded is absent, e.g. in CI — gate 1 still holds there)."""
    hero = dev_hero_frame_hashes(dev_encoded_dir)
    if not hero:
        return 0
    collisions: list[str] = []
    for path in sorted(p for p in pathlib.Path(out_dir).rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in hero:
            collisions.append(f"{path.relative_to(out_dir).as_posix()} == dev {hero[digest]}")
    if collisions:
        raise PublicLicenceError(
            "emitted files are byte-identical to CC BY-NC-ND dev hero frames:\n  "
            + "\n  ".join(collisions)
        )
    return len(hero)


# ------------------------------------------------------------------ public ramp specs

def public_specs(specs: dict[str, RampSpec]) -> dict[str, RampSpec]:
    """Apply the PUBLIC-CONTEXT overrides to the loaded ramps.yaml specs.

    Only t2m diverges: vmin widens 233.15 -> 203.15 K (module docstring). The
    override is legal only because the LUT bytes are range-independent for an
    opaque alpha policy — a ramp policy bakes vmin/vmax into the LUT's alpha
    channel at bake time, so reusing the LUT under a different range would lie.
    Asserted here so a future ramps.yaml edit cannot silently break that.
    """
    spec = specs["t2m"]
    if spec.alpha["policy"] != "opaque":
        raise EncodePublicError(
            "t2m alpha policy is no longer 'opaque' — the public vmin override "
            "reuses the baked LUT, which is only range-independent for opaque "
            "alpha. Re-derive the override before widening the range."
        )
    if not T2M_PUBLIC_VMIN < spec.vmin:
        raise EncodePublicError(
            f"public t2m floor {T2M_PUBLIC_VMIN} must sit below the hero-pair "
            f"floor {spec.vmin} — the override exists to widen, not to clip"
        )
    out = dict(specs)
    out["t2m"] = dataclasses.replace(spec, vmin=T2M_PUBLIC_VMIN)

    # msl: tighten the provisional ±80 hPa hero-side span to the measured public
    # span, keeping the midpoint EXACTLY at 1013.25 so the vik divergence stays
    # honest. Same opaque-alpha requirement as the t2m override.
    msl = specs["msl"]
    if msl.alpha["policy"] != "opaque":
        raise EncodePublicError(
            "msl alpha policy is no longer 'opaque' — the public range override "
            "reuses the baked LUT, which is only range-independent for opaque alpha."
        )
    midpoint = (msl.vmin + msl.vmax) / 2.0
    if abs(midpoint - 1013.25) > 1e-6:
        raise EncodePublicError(
            f"ramps.yaml msl midpoint {midpoint} != 1013.25 — the diverging ramp "
            "contract broke upstream; refusing to derive a public span from it."
        )
    out["msl"] = dataclasses.replace(
        msl, vmin=1013.25 - MSL_PUBLIC_SPAN_HPA, vmax=1013.25 + MSL_PUBLIC_SPAN_HPA
    )
    return out


# ------------------------------------------------------------------ output directory

def prepare_out_dir(out_dir: pathlib.Path) -> None:
    """Replace the managed entries wholesale; refuse anything this tool does not own.

    A stray file would make the whole-tree sha256 lie about what a re-run
    produces, so it is a loud error rather than a silent keep or delete.
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in MANAGED_ENTRIES:
        path = out_dir / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    strangers = sorted(p.name for p in out_dir.iterdir())
    if strangers:
        raise EncodePublicError(
            f"{out_dir} contains entries this tool does not manage: {strangers}. "
            "data/web is wholly owned by encode_public — delete them (or move them "
            "out) so the emitted tree hash means what it claims."
        )


# ------------------------------------------------------------------ sequence gates

def check_sequence(times: list[str]) -> list[int]:
    """The caption/data coupling gate. Returns the cache indices to publish.

    The generatedNote promises the Gaemi week at 12-hourly cadence; the cache
    must therefore be EXACTLY the 32-step 6-hourly sequence fetch_era5 was
    pointed at, and the published subset exactly its 00Z/12Z steps.
    """
    if times != CACHE_TIMES:
        raise EncodePublicError(
            f"raw cache times are not the Gaemi-week sequence the public caption "
            f"promises ({len(CACHE_TIMES)} 6-hourly steps {SEQUENCE_START}.."
            f"{SEQUENCE_END}); got {len(times)} steps "
            f"{times[0] if times else '—'}..{times[-1] if times else '—'}. "
            f"Refetch: python -m latentsky.fetch_era5 --start {SEQUENCE_START} "
            f"--end {SEQUENCE_END} --step-hours {CACHE_STEP_HOURS} --half-degree "
            f"--out data/dev/raw/{RAW_NAME}"
        )
    indices = [i for i, t in enumerate(times) if t in PUBLIC_TIMES]
    if [times[i] for i in indices] != PUBLIC_TIMES or len(indices) != len(PUBLIC_TIMES):
        raise EncodePublicError(
            f"12-hourly subselection failed: expected {len(PUBLIC_TIMES)} frames, "
            f"got {len(indices)} — the cache/public step constants disagree"
        )
    return indices


def check_half_grid(lat: np.ndarray, lon: np.ndarray) -> None:
    """The cache must be the exact 0.5 deg subgrid fetch_era5 --half-degree writes."""
    lat, lon = np.asarray(lat, dtype=np.float64), np.asarray(lon, dtype=np.float64)
    if lat.shape != (HALF_SHAPE[0],) or lat[0] != 90.0 or lat[-1] != -90.0 or not np.all(np.diff(lat) == -0.5):
        raise EncodePublicError(
            f"expected 0.5° latitude descending 90..-90 with {HALF_SHAPE[0]} rows, "
            f"got {lat[0]}..{lat[-1]} x{lat.size} — refetch with --half-degree"
        )
    if lon.shape != (HALF_SHAPE[1],) or lon[0] != 0.0 or not np.all(np.diff(lon) == 0.5):
        raise EncodePublicError(
            f"expected 0.5° longitude ascending 0..359.5 with {HALF_SHAPE[1]} cols, "
            f"got {lon[0]}..{lon[-1]} x{lon.size} — refetch with --half-degree"
        )


# ------------------------------------------------------------------ main encode

def encode_layers(
    raw_dir: pathlib.Path,
    out_dir: pathlib.Path,
    config: pathlib.Path = DEFAULT_CONFIG,
    lut_dir: pathlib.Path = DEFAULT_LUT_DIR,
    tiles_dir: pathlib.Path = basemap_mod.DEFAULT_TILES,
    dev_encoded_dir: pathlib.Path = DEV_ENCODED_DIR,
    coastline_path: pathlib.Path = basemap_mod.DEFAULT_COASTLINE,
) -> str:
    """Encode the publishable subset. Returns the whole-tree sha256 of --out."""
    t0 = time.perf_counter()
    raw_dir, out_dir = pathlib.Path(raw_dir), pathlib.Path(out_dir)

    npz_path = raw_dir / RAW_NAME
    if not npz_path.is_file():
        raise EncodePublicError(
            f"{npz_path} missing — fetch first: python -m latentsky.fetch_era5 "
            f"--start {SEQUENCE_START} --end {SEQUENCE_END} --step-hours "
            f"{CACHE_STEP_HOURS} --half-degree --out {npz_path}"
        )
    npz = np.load(npz_path)
    times = [str(t) for t in npz["times"]]
    lat, lon = npz["latitude"], npz["longitude"]

    public_idx = check_sequence(times)
    check_half_grid(lat, lon)

    specs = public_specs(load_ramps(config))
    prepare_out_dir(out_dir)

    # Vendor the LUTs the global layers use so the tree is self-contained.
    (out_dir / "luts").mkdir()
    luts: dict[str, tuple[np.ndarray, str, str]] = {}
    for variable in {p[1] for p in PUBLIC_PLAN}:
        src = lut_dir / specs[variable].lut_filename
        if not src.is_file():
            raise FileNotFoundError(
                f"{src} missing — bake the committed LUTs first: python -m latentsky.ramps"
            )
        rel = f"luts/{specs[variable].lut_filename}"
        shutil.copyfile(src, out_dir / rel)
        lut, sha = load_lut(out_dir / rel)
        luts[variable] = (lut, sha, rel)

    # The three global fields — wind speed is hypot(u, v) BEFORE ramping (§3.1),
    # in float64 exactly as encode_global derives it, so a future hero pair stays
    # comparable. The cache is already the exact 0.5° subgrid; only the roll to
    # −180..180 remains (an exact reindex, never interpolation).
    fields = {
        "wind10m": np.hypot(npz["u10m"].astype(np.float64), npz["v10m"].astype(np.float64)),
        "t2m": npz["t2m"].astype(np.float64),
        "tcwv": npz["tcwv"].astype(np.float64),
        "msl": npz["msl"].astype(np.float64) / 100.0,   # ARCO stores Pa; ramps are hPa
    }
    t2m_below = float((fields["t2m"] < specs["t2m"].vmin).mean() * 100.0)
    print(f"era5 ranges: wind10m {fields['wind10m'].min():.2f}..{fields['wind10m'].max():.2f} m/s | "
          f"t2m {fields['t2m'].min():.2f}..{fields['t2m'].max():.2f} K "
          f"({t2m_below:.4f}% below the public {specs['t2m'].vmin} K floor) | "
          f"tcwv {fields['tcwv'].min():.2f}..{fields['tcwv'].max():.2f} kg/m2 | "
          f"msl {fields['msl'].min():.2f}..{fields['msl'].max():.2f} hPa "
          f"(public span ±{MSL_PUBLIC_SPAN_HPA} about 1013.25)")

    records: list[LayerRecord] = []
    layer_bytes: dict[str, int] = {}
    for layer_id, variable, suffix in PUBLIC_PLAN:
        spec = specs[variable]
        lut, lut_sha, lut_rel = luts[variable]
        stack = fields[variable]
        if stack.shape != (len(times), *HALF_SHAPE):
            raise EncodePublicError(
                f"{variable}: expected {(len(times), *HALF_SHAPE)}, got {stack.shape}"
            )
        rolled, _ = roll_to_180(stack, lon)
        frames: list[str] = []
        total = 0
        for out_i, cache_i in enumerate(public_idx):
            rel = f"layers/{layer_id}/{out_i:03d}.webp"
            total += encode_frame(rolled[cache_i], lut, spec.vmin, spec.vmax, out_dir / rel)
            frames.append(rel)
        records.append(make_layer_record(
            layer_id=layer_id, kind="global", spec=spec, lut_sha256=lut_sha,
            lut_rel_path=lut_rel, rect=GLOBAL_GRID.rect, size=GLOBAL_GRID.size,
            frames=frames, label_suffix=suffix,
        ))
        layer_bytes[layer_id] = total
        print(f"  {layer_id:16s} global      {GLOBAL_GRID.width:4d}x{GLOBAL_GRID.height:<4d} "
              f"{len(frames)} frames  {total:>9,} B  ({total / len(frames):,.0f} B/frame)")

    # LICENCE GATE 1 — before any manifest can exist.
    verify_publishable(records)

    # The dark basemap — Natural Earth II + 110m coastlines, public domain,
    # shippable. Baked fresh every run (basemap.bake proves its own determinism
    # by double-encoding).
    size = basemap_mod.bake(tiles_dir, out_dir / BASEMAP_REL, coastline_path=coastline_path)
    print(f"  basemap baked: {BASEMAP_REL}  {size:,} B  "
          f"({basemap_mod.WIDTH}x{basemap_mod.HEIGHT} WebP q{basemap_mod.QUALITY}, lossy — scenery, not data)")

    manifest = build_manifest(
        dict(RUN), PUBLIC_TIMES, records, specs,
        basemap={"global": BASEMAP_REL, "globalRect": GLOBAL_GRID.rect},
    )
    written = write_manifest(manifest, out_dir)
    print(f"\npublic manifest validated against schema and written: {written}")
    print(f"  layers: {len(records)} global, 0 hero | frames: {len(PUBLIC_TIMES)} "
          f"(12-hourly from a {len(times)}-step 6-hourly cache) | basemap: {BASEMAP_REL}")

    # LICENCE GATE 2 — the emitted tree against the local dev hero frames.
    compared = verify_no_hero_bytes(out_dir, dev_encoded_dir)
    if compared:
        print(f"licence gate: no emitted file matches any of the {compared} local "
              f"dev hero frames (byte-identity check)")
    else:
        print("licence gate: local dev hero frames absent — byte-identity check "
              "skipped (kind gate still enforced)")

    print("\nper-tree sha256:")
    for rel in [*(f"layers/{p[0]}" for p in PUBLIC_PLAN), "basemap", "luts"]:
        print(f"  {rel + '/':32s} {tree_sha256(out_dir / rel)}")
    whole = tree_sha256(out_dir)
    print(f"  {'<entire output tree>':32s} {whole}")

    print(f"\nencode wall time: {time.perf_counter() - t0:.1f} s")
    # Transfer-size report: every file in this tree ships at its on-disk size —
    # WebP/PNG are already compressed and CloudFront never recompresses images
    # (§9.2), so disk bytes ARE the transfer bytes here.
    total, within = budget.report(out_dir)
    print(f"transfer size (uncompressed formats — disk bytes = transfer bytes): "
          f"{total:,} B ({total / budget.MB:.3f} MB)")
    if not within:
        raise SystemExit(1)
    return whole


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    ap.add_argument("--luts", type=pathlib.Path, default=DEFAULT_LUT_DIR)
    ap.add_argument("--tiles", type=pathlib.Path, default=basemap_mod.DEFAULT_TILES)
    args = ap.parse_args(argv)
    encode_layers(args.raw, args.out, args.config, args.luts, args.tiles)


if __name__ == "__main__":
    main()
