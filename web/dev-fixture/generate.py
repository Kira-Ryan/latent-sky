"""Generate the synthetic smoke-test fixture for the Latent Sky web app.

Entirely synthetic — analytic fields, no weather data, no CC BY-NC-ND inputs —
so everything under web/dev-fixture/ is safe to commit.

Produces, per Architecture.md sections 5.5, 7.1 and 7.2:
  luts/<var>.lut.png       256x1 RGBA LUTs (batlowK wind10m, thermal t2m, davos tcwv)
  frames/*.webp            2 frames x {fine 64x64, coarse 8x8} hero layers for
                           wind10m + t2m, plus full-globe "global" layers for
                           wind10m (global + hero together) and tcwv (global
                           only), WebP lossless with exact alpha preservation
  basemap/global-dark.webp tiny dark synthetic full-globe basemap (manifest
                           "basemap" object — exercises the basemap plumbing)
  manifest.json            conforming to schema/manifest.schema.json

Round-trip bit-identity of every WebP is asserted (encode -> decode -> compare),
which proves lossless=True + exact=True on the installed Pillow.

Run:  python dev-fixture/generate.py   (from web/)
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
LUTS = HERE / "luts"
FRAMES = HERE / "frames"
BASEMAP = HERE / "basemap"
SCHEMA = HERE.parent.parent / "schema" / "manifest.schema.json"

# Measured CorrDiffTaiwan output extent, Architecture.md section 3.4 (public numbers).
RECT = [116.1372, 19.5187, 125.5459, 27.9282]  # west, south, east, north
GLOBE = [-180.0, -90.0, 180.0, 90.0]

# Three frames, not two: with n frames the cross-fade index i spans [0, n-2],
# so n >= 3 is the minimum that can exercise the slot-rotation path (advance by
# exactly one frame) that band-ordered insertion must survive.
FRAME_TIMES = ["2026-01-01T00:00:00Z", "2026-01-01T06:00:00Z", "2026-01-01T12:00:00Z"]

FINE = 64
COARSE = 8
GLOBAL_W, GLOBAL_H = 72, 36  # deliberately non-power-of-two (section 6.1)
BASEMAP_W, BASEMAP_H = 144, 72

# Per-variable colour contract — Architecture.md section 7.1. vmin/vmax are GLOBAL.
# "hero"/"global" pick which layer kinds the fixture emits: wind10m has both
# (global under the hero pair), t2m is hero-only here, tcwv is global-only —
# together they exercise every compositing combination the app supports.
VARS = {
    "wind10m": {
        "label": "10 m wind speed",
        "units": "m/s",
        "vmin": 0.0,
        "vmax": 55.0,
        "alpha_policy": "0 below 2 m/s, ramping to 1 by 6 m/s",
        "hero": True,
        "global": True,
    },
    "t2m": {
        "label": "2 m temperature",
        "units": "K",
        "vmin": 233.15,
        "vmax": 323.15,
        "alpha_policy": "opaque",
        "hero": True,
        "global": False,
    },
    "tcwv": {
        "label": "Total column water vapour",
        "units": "kg/m²",
        "vmin": 0.0,
        "vmax": 70.0,
        "alpha_policy": "opaque",
        "hero": False,
        "global": True,
    },
}


def bake_lut(var: str) -> np.ndarray:
    """256x1 RGBA uint8 LUT from the real ramp libraries, with the alpha policy baked in."""
    x = np.linspace(0.0, 1.0, 256)
    if var == "wind10m":
        from cmcrameri import cm as crameri

        rgba = (crameri.batlowK(x) * 255.0 + 0.5).astype(np.uint8)  # (256, 4)
        v = x * (VARS[var]["vmax"] - VARS[var]["vmin"]) + VARS[var]["vmin"]  # m/s
        alpha = np.clip((v - 2.0) / (6.0 - 2.0), 0.0, 1.0)
        rgba[:, 3] = (alpha * 255.0 + 0.5).astype(np.uint8)
    elif var == "t2m":
        import cmocean

        rgba = (cmocean.cm.thermal(x) * 255.0 + 0.5).astype(np.uint8)
        rgba[:, 3] = 255
    elif var == "tcwv":
        from cmcrameri import cm as crameri

        rgba = (crameri.davos(x) * 255.0 + 0.5).astype(np.uint8)
        rgba[:, 3] = 255
    else:
        raise ValueError(f"no ramp defined for {var}")
    return rgba


def save_lut(var: str, rgba: np.ndarray) -> Path:
    out = LUTS / f"{var}.lut.png"
    Image.fromarray(rgba.reshape(1, 256, 4), mode="RGBA").save(out, format="PNG")
    return out


def synthetic_field(var: str, frame: int, n: int) -> np.ndarray:
    """Analytic field on an n x n grid. Frame-dependent so scrubbing changes pixels."""
    yy, xx = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n), indexing="ij")
    if var == "wind10m":
        # A gaussian 'storm' that moves between frames over a weak swirl background.
        cx, cy = 0.35 + 0.135 * frame, 0.40 + 0.09 * frame
        r2 = (xx - cx) ** 2 + (yy - cy) ** 2
        storm = 48.0 * np.exp(-r2 / 0.012)
        swirl = 3.0 + 2.5 * np.sin(8.0 * xx + frame) * np.cos(7.0 * yy)
        field = np.maximum(storm, swirl)
        # A calm corner below the 2 m/s alpha floor so transparency is exercised.
        calm = np.exp(-(((xx - 0.9) ** 2 + (yy - 0.1) ** 2) / 0.02))
        return field * (1.0 - 0.95 * calm)
    if var == "t2m":
        warm_x = 0.3 + 0.3 * frame
        grad = 285.0 + 18.0 * (1.0 - yy)
        blob = 9.0 * np.exp(-(((xx - warm_x) ** 2 + (yy - 0.5) ** 2) / 0.03))
        return grad + blob
    raise ValueError(var)


def synthetic_global_field(var: str, frame: int, w: int, h: int) -> np.ndarray:
    """Analytic full-globe field, (h, w), row 0 = north pole. Frame-dependent."""
    lat = np.linspace(90.0, -90.0, h)[:, None]  # degrees
    lon = np.linspace(-180.0, 180.0, w, endpoint=False)[None, :]
    if var == "wind10m":
        # Two mid-latitude storm-track bands plus a storm that moves east
        # between frames; calm tropics exercise the alpha tail globally.
        jets = 16.0 * np.exp(-(((np.abs(lat) - 45.0) / 14.0) ** 2))
        storm_lon = -60.0 + 40.0 * frame
        storm = 30.0 * np.exp(-(((lon - storm_lon) / 25.0) ** 2 + ((lat - 40.0) / 12.0) ** 2))
        ripple = 2.0 * np.sin(np.radians(lon * 3.0 + frame * 40.0)) * np.cos(np.radians(lat * 2.0))
        return np.maximum(jets + ripple, storm)
    if var == "tcwv":
        # Moist tropics drying towards the poles, with a moisture plume that
        # advances between frames.
        base = 52.0 * np.exp(-((lat / 32.0) ** 2)) + 4.0
        plume_lon = 100.0 + 35.0 * frame
        plume = 16.0 * np.exp(-(((lon - plume_lon) / 30.0) ** 2 + ((lat - 15.0) / 14.0) ** 2))
        waves = 3.0 * np.sin(np.radians(lon * 2.0 - frame * 55.0)) * np.exp(-((lat / 45.0) ** 2))
        return base + plume + waves
    raise ValueError(var)


def make_basemap(out: Path) -> int:
    """Tiny dark synthetic full-globe basemap: latitude shading + a graticule.

    Entirely synthetic (no Natural Earth pixels, nothing licence-bearing) and
    deliberately dark, matching the designed-quiet globe (section 9.5). Opaque
    RGBA, lossless WebP.
    """
    lat = np.linspace(90.0, -90.0, BASEMAP_H)[:, None]
    lon = np.linspace(-180.0, 180.0, BASEMAP_W, endpoint=False)[None, :]
    shade = np.cos(np.radians(lat)) * np.ones_like(lon)  # brighter equator, dark poles
    r = 11 + 10 * shade
    g = 14 + 13 * shade
    b = 20 + 18 * shade
    # Faint 30-degree graticule so the smoke test's "not a uniform colour"
    # check sees structure from the basemap alone.
    on_meridian = np.minimum(np.mod(lon, 30.0), 30.0 - np.mod(lon, 30.0)) < 1.5
    on_parallel = np.minimum(np.mod(lat, 30.0), 30.0 - np.mod(lat, 30.0)) < 1.5
    grid = (on_meridian | on_parallel) & np.ones_like(shade, dtype=bool)
    rgba = np.stack(
        [
            np.where(grid, r + 14, r),
            np.where(grid, g + 16, g),
            np.where(grid, b + 20, b),
            np.full_like(r, 255.0),
        ],
        axis=-1,
    ).astype(np.uint8)
    Image.fromarray(rgba, mode="RGBA").save(out, format="WEBP", lossless=True, exact=True, method=6)
    return out.stat().st_size


def encode_webp(field: np.ndarray, lut: np.ndarray, vmin: float, vmax: float, out: Path) -> int:
    """field -> uint8 index -> shared LUT -> WebP lossless. Mirrors pipeline encode.py."""
    idx = np.clip((field - vmin) / (vmax - vmin), 0.0, 1.0)
    idx = (idx * 255.0 + 0.5).astype(np.uint8)
    rgba = lut[idx]  # (n, n, 4) uint8
    Image.fromarray(rgba, mode="RGBA").save(
        out, format="WEBP", lossless=True, exact=True, method=6
    )
    # PROVE round-trip bit-identity, including RGB under alpha=0 (the 'exact' guarantee).
    back = np.asarray(Image.open(out).convert("RGBA"))
    if not np.array_equal(back, rgba):
        diff = int(np.count_nonzero(back != rgba))
        raise AssertionError(
            f"WebP round-trip NOT bit-identical for {out.name}: {diff} differing bytes. "
            "The installed Pillow does not honour exact=True — an alternative encoder is required."
        )
    return out.stat().st_size


def identity_checksum(var: str, lut_path: Path) -> str:
    """sha256 of the canonical (variable, lut sha256, vmin, vmax, alphaPolicy) tuple."""
    spec = VARS[var]
    lut_sha = hashlib.sha256(lut_path.read_bytes()).hexdigest()
    canonical = json.dumps(
        [var, lut_sha, spec["vmin"], spec["vmax"], spec["alpha_policy"]],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> None:
    LUTS.mkdir(parents=True, exist_ok=True)
    FRAMES.mkdir(parents=True, exist_ok=True)
    BASEMAP.mkdir(parents=True, exist_ok=True)

    layers: dict[str, dict] = {}
    total = 0

    for var, spec in VARS.items():
        lut = bake_lut(var)
        lut_path = save_lut(var, lut)
        identity = identity_checksum(var, lut_path)
        print(f"[lut] {lut_path.name}  identity={identity[:16]}...")

        if spec["hero"]:
            for kind, n in (("hero-fine", FINE), ("hero-coarse", COARSE)):
                paths = []
                for f in range(len(FRAME_TIMES)):
                    fine = synthetic_field(var, f, FINE)
                    if n == FINE:
                        field = fine
                    else:
                        # Coarse = block mean of the same fine field, like a coarse model view.
                        field = fine.reshape(COARSE, FINE // COARSE, COARSE, FINE // COARSE).mean(
                            axis=(1, 3)
                        )
                    name = f"{var}-{kind}-{f:03d}.webp"
                    size = encode_webp(field, lut, spec["vmin"], spec["vmax"], FRAMES / name)
                    total += size
                    paths.append(f"frames/{name}")
                    print(f"[webp] {name:36s} {n}x{n}  {size:5d} B  round-trip bit-identical")

                layer_id = f"{var}-{kind}"
                layers[layer_id] = {
                    "kind": kind,
                    "variable": var,
                    "label": spec["label"] + (" — coarse" if kind == "hero-coarse" else " — generated"),
                    "units": spec["units"],
                    "rect": RECT,
                    "size": [n, n],
                    "lut": f"luts/{var}.lut.png",
                    "vmin": spec["vmin"],
                    "vmax": spec["vmax"],
                    "identity": identity,
                    "frames": paths,
                    "pairWith": f"{var}-hero-coarse" if kind == "hero-fine" else f"{var}-hero-fine",
                }

        if spec["global"]:
            # Full-globe layer through the SAME lut/vmin/vmax/alpha tuple, so its
            # identity string equals the hero layers' — asserted by the app at load.
            paths = []
            for f in range(len(FRAME_TIMES)):
                field = synthetic_global_field(var, f, GLOBAL_W, GLOBAL_H)
                name = f"{var}-global-{f:03d}.webp"
                size = encode_webp(field, lut, spec["vmin"], spec["vmax"], FRAMES / name)
                total += size
                paths.append(f"frames/{name}")
                print(f"[webp] {name:36s} {GLOBAL_W}x{GLOBAL_H}  {size:5d} B  round-trip bit-identical")

            layers[f"{var}-global"] = {
                "kind": "global",
                "variable": var,
                "label": spec["label"] + " — global",
                "units": spec["units"],
                "rect": GLOBE,
                "size": [GLOBAL_W, GLOBAL_H],
                "lut": f"luts/{var}.lut.png",
                "vmin": spec["vmin"],
                "vmax": spec["vmax"],
                "identity": identity,
                "frames": paths,
            }

    basemap_path = BASEMAP / "global-dark.webp"
    basemap_size = make_basemap(basemap_path)
    total += basemap_size
    print(f"[webp] {basemap_path.name:36s} {BASEMAP_W}x{BASEMAP_H}  {basemap_size:5d} B  dark synthetic basemap")

    manifest = {
        "schemaVersion": 1,
        "run": {
            "id": "dev-fixture-synthetic",
            "kind": "dev-sample",
            "model": {"prognostic": "synthetic", "downscaling": "synthetic"},
            "generatedNote": (
                "Synthetic smoke-test fixture — analytic fields, not weather. "
                "Nothing on this globe is observed, forecast or generated by a model."
            ),
            # First-class run hints (schema run.*) so the web parsing of all
            # three optional fields is exercised by the smoke test. Honest
            # values: the 'storm' is an analytic gaussian, and frame 2 is its
            # most developed frame (it grows monotonically with frame index).
            "stormName": "Synthetic vortex",
            "heroFrame": 2,
            "placeLabel": "Synthetic test domain",
        },
        "frames": FRAME_TIMES,
        "basemap": {
            "global": "basemap/global-dark.webp",
            "globalRect": GLOBE,
        },
        "layers": layers,
    }

    out = HERE / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[manifest] {out}  ({out.stat().st_size} B, frames total {total} B)")

    # Formal validation against THE contract, if jsonschema is available.
    try:
        import jsonschema
    except ImportError:
        print("[schema] jsonschema not installed — formal validation SKIPPED (structural checks in the app still apply)")
        return
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(manifest, schema)
    print(f"[schema] manifest validates against {SCHEMA.name} (jsonschema {jsonschema.__version__})")


if __name__ == "__main__":
    sys.exit(main())
