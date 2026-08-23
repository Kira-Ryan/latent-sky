"""Encode the PUBLISHABLE dataset into data/web — the committed, deployed artefact.

This is the pre-forecast public subset ONLY (Architecture.md §11, §12):

  * the three global ERA5 layers   wind10m / t2m / tcwv, 720x361, ERA5 is CC-BY
  * the dark basemap               Natural Earth II, public domain
  * the LUT PNGs                   baked from MIT colour maps
  * manifest.json                  run.kind "dev-sample", NO hero layers

The hero layers under data/dev derive from the CorrDiff package's CC BY-NC-ND 4.0
sample dataset and must NEVER appear in anything committed or deployed. This tool
therefore never reads cwb_sample.npz and enforces the boundary twice, belt and
braces (LICENCE GATE below):

  1. verify_publishable(): the build FAILS if any emitted layer kind is not in
     PUBLISHABLE_KINDS — a hero-* kind can never reach the manifest.
  2. verify_no_hero_bytes(): when data/dev/encoded exists locally, every emitted
     file is hashed against every dev hero frame and the build FAILS on any
     byte-identical match. tests/test_encode_public.py proves the same property
     over the committed data/web tree.

Determinism: every input is a cached file (era5_global.npz), a committed config
(ramps.yaml), a committed LUT bake, or the vendored Natural Earth II tiles, and
every encoder parameter is fixed — a re-run must reproduce the tree
byte-for-byte. The managed output entries are replaced wholesale on every run
and the tool refuses to run over a directory carrying anything else, so the
printed whole-tree sha256 is honestly comparable between runs.

Usage:
    python -m latentsky.encode_public --raw data/dev/raw --out data/web
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
from . import budget
from .encode import LayerRecord, encode_frame, load_lut, make_layer_record
from .encode_global import (
    BASEMAP_REL,
    ERA5_SHAPE,
    GLOBAL_GRID,
    GLOBAL_PLAN,
    prepare_global,
    tree_sha256,
)
from .manifest import build_manifest, write_manifest
from .ramps import DEFAULT_CONFIG, DEFAULT_LUT_DIR, load_ramps

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEV_ENCODED_DIR = REPO_ROOT / "data" / "dev" / "encoded"

# The ONLY layer kinds this tool may emit. hero-fine / hero-coarse derive from
# CC BY-NC-ND data until the first real forecast run and are non-publishable.
PUBLISHABLE_KINDS = frozenset({"global"})

# Everything encode_public owns inside --out. Replaced wholesale each run;
# anything else present is a loud error, never silently kept or deleted.
MANAGED_ENTRIES = ("layers", "luts", "basemap", "manifest.json")

# The note gates below (exactly five timesteps, all in 2021) exist because this
# string asserts both facts — the caption and the data cannot drift apart.
GENERATED_NOTE = (
    "Global fields are ERA5 reanalysis (five 2021 timesteps), rendered through "
    "the Latent Sky pipeline. The kilometre-scale AI-generated hero layer "
    "arrives with the first forecast run. Contains modified Copernicus Climate "
    "Change Service information 2021."
)

RUN = {
    "id": "public-era5-2021",
    "kind": "dev-sample",
    "model": {
        "prognostic": "ERA5 reanalysis (ECMWF, anonymous ARCO mirror) — "
                      "analysis, not a model forecast",
        "downscaling": "none yet — the CorrDiffTaiwan ~2 km hero layer arrives "
                       "with the first forecast run",
    },
    "generatedNote": GENERATED_NOTE,
    # stormName / heroFrame / placeLabel deliberately absent: they describe the
    # hero experience, which does not exist in the pre-forecast public subset.
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


# ------------------------------------------------------------------ main encode

def encode_layers(
    raw_dir: pathlib.Path,
    out_dir: pathlib.Path,
    config: pathlib.Path = DEFAULT_CONFIG,
    lut_dir: pathlib.Path = DEFAULT_LUT_DIR,
    tiles_dir: pathlib.Path = basemap_mod.DEFAULT_TILES,
    dev_encoded_dir: pathlib.Path = DEV_ENCODED_DIR,
) -> str:
    """Encode the publishable subset. Returns the whole-tree sha256 of --out."""
    t0 = time.perf_counter()
    raw_dir, out_dir = pathlib.Path(raw_dir), pathlib.Path(out_dir)

    npz_path = raw_dir / "era5_global.npz"
    if not npz_path.is_file():
        raise EncodePublicError(
            f"{npz_path} missing — fetch first: python -m latentsky.fetch_era5 "
            f"--meta {raw_dir / 'dev_meta.json'} --out {npz_path}"
        )
    npz = np.load(npz_path)
    times = [str(t) for t in npz["times"]]
    lat, lon = npz["latitude"], npz["longitude"]

    # The generatedNote promises "five 2021 timesteps" — hold the data to it.
    if len(times) != 5:
        raise EncodePublicError(
            f"expected exactly five timesteps (the public caption says so), got "
            f"{len(times)}: {times}"
        )
    wrong_year = [t for t in times if not t.startswith("2021-")]
    if wrong_year:
        raise EncodePublicError(
            f"expected 2021 timesteps (the public caption says so), got {wrong_year}"
        )

    specs = load_ramps(config)
    prepare_out_dir(out_dir)

    # Vendor the LUTs the global layers use so the tree is self-contained.
    (out_dir / "luts").mkdir()
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

    # The three global fields — wind speed is hypot(u, v) BEFORE ramping (§3.1),
    # exactly as encode_global derives it, so a future hero pair stays comparable.
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

    records: list[LayerRecord] = []
    for layer_id, variable, suffix in GLOBAL_PLAN:
        spec = specs[variable]
        lut, lut_sha, lut_rel = luts[variable]
        stack = fields[variable]
        if stack.shape != (len(times), *ERA5_SHAPE):
            raise EncodePublicError(
                f"{variable}: expected {(len(times), *ERA5_SHAPE)}, got {stack.shape}"
            )
        frames: list[str] = []
        total = 0
        for i in range(len(times)):
            rel = f"layers/{layer_id}/{i:03d}.webp"
            field = prepare_global(stack[i], lat, lon)
            total += encode_frame(field, lut, spec.vmin, spec.vmax, out_dir / rel)
            frames.append(rel)
        records.append(make_layer_record(
            layer_id=layer_id, kind="global", spec=spec, lut_sha256=lut_sha,
            lut_rel_path=lut_rel, rect=GLOBAL_GRID.rect, size=GLOBAL_GRID.size,
            frames=frames, label_suffix=suffix,
        ))
        print(f"  {layer_id:16s} global      {GLOBAL_GRID.width:4d}x{GLOBAL_GRID.height:<4d} "
              f"{len(frames)} frames  {total:>9,} B  ({total / len(frames):,.0f} B/frame)")

    # LICENCE GATE 1 — before any manifest can exist.
    verify_publishable(records)

    # The dark basemap — Natural Earth II, public domain, shippable. Baked fresh
    # every run (basemap.bake proves its own determinism by double-encoding).
    size = basemap_mod.bake(tiles_dir, out_dir / BASEMAP_REL)
    print(f"  basemap baked: {BASEMAP_REL}  {size:,} B  "
          f"({basemap_mod.WIDTH}x{basemap_mod.HEIGHT} WebP q{basemap_mod.QUALITY}, lossy — scenery, not data)")

    manifest = build_manifest(
        dict(RUN), times, records, specs,
        basemap={"global": BASEMAP_REL, "globalRect": GLOBAL_GRID.rect},
    )
    written = write_manifest(manifest, out_dir)
    print(f"\npublic manifest validated against schema and written: {written}")
    print(f"  layers: {len(records)} global, 0 hero | frames: {len(times)} | "
          f"basemap: {BASEMAP_REL}")

    # LICENCE GATE 2 — the emitted tree against the local dev hero frames.
    compared = verify_no_hero_bytes(out_dir, dev_encoded_dir)
    if compared:
        print(f"licence gate: no emitted file matches any of the {compared} local "
              f"dev hero frames (byte-identity check)")
    else:
        print("licence gate: local dev hero frames absent — byte-identity check "
              "skipped (kind gate still enforced)")

    print("\nper-tree sha256:")
    for rel in [*(f"layers/{p[0]}" for p in GLOBAL_PLAN), "basemap", "luts"]:
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
