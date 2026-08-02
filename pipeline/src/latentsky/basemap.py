"""Bake the dark global basemap from Cesium's bundled Natural Earth II tiles — §9.5.

Source: web/node_modules/cesium/Build/Cesium/Assets/Textures/NaturalEarthII —
public domain, so the baked basemap is shippable (the manifest schema requires
basemap imagery to be public-domain or CC-BY). The tiles are TMS, geodetic
profile, and the TMS y-axis is FLIPPED relative to slippy tiles: y=0 is the
SOUTHERNMOST row (tilemapresource.xml: Origin y=-90). Zoom 2 is 8x4 tiles of
256 px = 2048x1024 for the full [-180,-90,180,90] rect.

Styling is OFFLINE AUTHORING of scenery, not a per-layer runtime colour
adjustment — §7.2(c)'s "never touch brightness/saturation on either layer"
applies to the data layers at view time, not to baking a basemap. The palette
comes from DOCS/concept-visual.html :root: ocean near --bg #070c1a, land a
quiet desaturated blue-grey #16223c..#22304f (--line), coastlines legible from
the value step alone. Nothing saturated: the data field must be the only loud
thing on the planet.

WebP lossy q90 is acceptable here and only here: the basemap is scenery, not
data. Colour identity (§8's lossless-exact mandate) protects the LUT-ramped
data layers; the basemap sits underneath them and carries no encoded values.

CLI:
    python -m latentsky.basemap [--tiles <NaturalEarthII dir>] [--out <webp>]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import pathlib

import numpy as np
from PIL import Image

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_TILES = (
    REPO_ROOT / "web" / "node_modules" / "cesium" / "Build" / "Cesium"
    / "Assets" / "Textures" / "NaturalEarthII"
)
DEFAULT_OUT = REPO_ROOT / "data" / "dev" / "encoded" / "basemap" / "global-dark.webp"

ZOOM = 2
TILE_PX = 256
COLS, ROWS = 8, 4                       # zoom 2, geodetic: 45 deg per tile
WIDTH, HEIGHT = COLS * TILE_PX, ROWS * TILE_PX  # 2048 x 1024
QUALITY = 90

# Palette — DOCS/concept-visual.html :root (--bg, --line) plus in-band midpoints.
OCEAN_DEEP = "#070c1a"    # --bg: open ocean floor colour
OCEAN_SHELF = "#0b1226"   # slight lift over bright shallow shelves
LAND_DARK = "#16223c"     # dark vegetated land
LAND_BRIGHT = "#22304f"   # --line: deserts and ice caps — the ceiling; nothing brighter

# Classifier constants, calibrated on the real zoom-2 tiles (2026-08-02):
# NE2 ocean is strongly blue (B - max(R,G) ~ +30..+40), land is green/tan
# (negative), ice caps sit at +6..+10 and correctly land on the land side.
_OCEAN_BLUENESS_LO, _OCEAN_BLUENESS_SPAN = 4.0, 12.0
_OCEAN_LUM_LO, _OCEAN_LUM_SPAN = 70.0, 70.0     # source luminance -> shelf lift
_LAND_LUM_LO, _LAND_LUM_SPAN = 90.0, 130.0      # source luminance -> land brightness


class BasemapError(RuntimeError):
    """The basemap could not be baked. Nothing partial is written."""


def _hex_rgb(s: str) -> np.ndarray:
    return np.array([int(s[i:i + 2], 16) for i in (1, 3, 5)], dtype=np.float64)


def _smoothstep(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def stitch(tiles_root: pathlib.Path = DEFAULT_TILES) -> np.ndarray:
    """Stitch TMS zoom `ZOOM` into (1024, 2048, 3) uint8, row 0 = north.

    TMS y counts FROM THE SOUTH, so tile y lands at image block row (ROWS-1-y).
    """
    zoom_dir = pathlib.Path(tiles_root) / str(ZOOM)
    if not zoom_dir.is_dir():
        raise BasemapError(f"tile zoom directory missing: {zoom_dir}")
    out = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    for x in range(COLS):
        for tms_y in range(ROWS):
            path = zoom_dir / str(x) / f"{tms_y}.jpg"
            if not path.is_file():
                raise BasemapError(f"tile missing: {path}")
            tile = Image.open(path).convert("RGB")
            if tile.size != (TILE_PX, TILE_PX):
                raise BasemapError(f"{path}: expected {TILE_PX}x{TILE_PX}, got {tile.size}")
            row0 = (ROWS - 1 - tms_y) * TILE_PX     # the TMS flip
            out[row0:row0 + TILE_PX, x * TILE_PX:(x + 1) * TILE_PX] = np.asarray(tile)
    return out


def style_dark(rgb: np.ndarray) -> np.ndarray:
    """Restyle NE2 colours into the project's dark palette. (H, W, 3) uint8 -> uint8.

    Per-pixel: a blueness signal (B - max(R,G)) separates ocean from land (ice
    caps fall on the land side); source luminance modulates WITHIN each class —
    ocean lifts slightly over shallow shelves, land brightens toward deserts and
    ice. Every output pixel is a blend of the four palette anchors, so the
    result is bounded by them: nothing saturated can come out.
    """
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3) uint8 RGB, got {rgb.dtype} {rgb.shape}")
    a = rgb.astype(np.float64)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    blueness = b - np.maximum(r, g)

    oceanness = _smoothstep((blueness - _OCEAN_BLUENESS_LO) / _OCEAN_BLUENESS_SPAN)
    ocean = _lerp(_hex_rgb(OCEAN_DEEP), _hex_rgb(OCEAN_SHELF),
                  _smoothstep((lum - _OCEAN_LUM_LO) / _OCEAN_LUM_SPAN))
    land = _lerp(_hex_rgb(LAND_DARK), _hex_rgb(LAND_BRIGHT),
                 _smoothstep((lum - _LAND_LUM_LO) / _LAND_LUM_SPAN))
    out = land * (1.0 - oceanness[..., None]) + ocean * oceanness[..., None]
    return np.clip(out + 0.5, 0.0, 255.0).astype(np.uint8)


def _lerp(c0: np.ndarray, c1: np.ndarray, t: np.ndarray) -> np.ndarray:
    return c0[None, None, :] + (c1 - c0)[None, None, :] * t[..., None]


def _encode_webp(styled: np.ndarray, quality: int) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(styled, mode="RGB").save(
        buf, format="WEBP", lossless=False, quality=quality, method=6
    )
    return buf.getvalue()


def bake(
    tiles_root: pathlib.Path = DEFAULT_TILES,
    out_path: pathlib.Path = DEFAULT_OUT,
    quality: int = QUALITY,
) -> int:
    """Stitch -> style -> WebP q90. Returns the byte size written.

    Matches ramps.bake's discipline: the image is encoded twice in memory and
    the bake fails loudly if the two encodes differ, so non-determinism can
    never land silently.
    """
    styled = style_dark(stitch(tiles_root))
    first = _encode_webp(styled, quality)
    second = _encode_webp(styled, quality)
    if first != second:
        raise BasemapError("basemap WebP encode is non-deterministic: two encodes differ")
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(first)
    return len(first)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tiles", type=pathlib.Path, default=DEFAULT_TILES)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--quality", type=int, default=QUALITY)
    args = ap.parse_args(argv)

    size = bake(args.tiles, args.out, args.quality)
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    print(f"baked {args.out} — {WIDTH}x{HEIGHT} WebP q{args.quality}, "
          f"{size:,} B, sha256 {digest}")


if __name__ == "__main__":
    main()
