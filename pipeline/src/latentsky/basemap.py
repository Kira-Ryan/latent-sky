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
starts from DOCS/concept-visual.html :root: ocean near --bg #070c1a, land a
quiet desaturated blue-grey. Live-site feedback (2026-08): continents were
illegible under the opaque data layers, so the land anchors sit at roughly
TWICE their original distance from the ocean floor — still desaturated
blue-grey, nothing saturated: the data field must be the only loud thing on
the planet.

Coastlines: Natural Earth 110m physical coastline vectors (public domain,
cached at pipeline/assets/ne_110m_coastline.geojson, audited in
licences/MANIFEST.yaml) drawn as a subtle 1 px line just above the land
ceiling, so shorelines stay legible even where a translucent field sits on
top. The land ceiling is deliberately held BELOW the coastline colour.

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
import json
import pathlib

import numpy as np
from PIL import Image, ImageDraw

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_TILES = (
    REPO_ROOT / "web" / "node_modules" / "cesium" / "Build" / "Cesium"
    / "Assets" / "Textures" / "NaturalEarthII"
)
DEFAULT_OUT = REPO_ROOT / "data" / "dev" / "encoded" / "basemap" / "global-dark.webp"
# Natural Earth 110m coastline (public domain) — the cached copy this repo audits.
DEFAULT_COASTLINE = pathlib.Path(__file__).resolve().parents[2] / "assets" / "ne_110m_coastline.geojson"

ZOOM = 2
TILE_PX = 256
COLS, ROWS = 8, 4                       # zoom 2, geodetic: 45 deg per tile
WIDTH, HEIGHT = COLS * TILE_PX, ROWS * TILE_PX  # 2048 x 1024
QUALITY = 90

# Palette — dark family from DOCS/concept-visual.html :root (--bg #070c1a).
# Land/ocean contrast DOUBLED against the original bake (live-site feedback:
# continents were illegible under the opaque wind layer): LAND_DARK sits at
# exactly 2x its original offset from OCEAN_DEEP (#16223c -> #25385e); the
# LAND_BRIGHT ceiling is raised proportionally but capped BELOW the coastline
# colour so the 1 px shoreline reads above every land pixel.
OCEAN_DEEP = "#070c1a"    # --bg: open ocean floor colour
OCEAN_SHELF = "#0b1226"   # slight lift over bright shallow shelves
LAND_DARK = "#25385e"     # dark vegetated land — 2x the original #16223c offset
LAND_BRIGHT = "#35496e"   # deserts and ice caps — the ceiling; below the coastline
COASTLINE_COLOUR = "#3a4f7a"  # 1 px shoreline — the brightest thing on the basemap

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


# ------------------------------------------------------------------ coastlines

def load_coastlines(path: pathlib.Path = DEFAULT_COASTLINE) -> list[list[tuple[float, float]]]:
    """Read the Natural Earth coastline GeoJSON -> list of (lon, lat) polylines.

    The 110m physical coastline is a FeatureCollection of LineStrings whose
    coordinates are already split at the antimeridian (verified on the cached
    copy: zero segments jump more than 180 deg of longitude). Anything else in
    the file is a loud error — a silently skipped geometry would present as a
    missing shoreline.
    """
    path = pathlib.Path(path)
    if not path.is_file():
        raise BasemapError(
            f"coastline GeoJSON missing: {path} — the Natural Earth 110m coastline "
            "is cached under pipeline/assets/ (public domain, see licences/MANIFEST.yaml)"
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("type") != "FeatureCollection":
        raise BasemapError(f"{path}: expected a FeatureCollection, got {doc.get('type')!r}")
    lines: list[list[tuple[float, float]]] = []
    for i, feature in enumerate(doc.get("features", [])):
        geom = feature.get("geometry") or {}
        if geom.get("type") != "LineString":
            raise BasemapError(
                f"{path}: feature {i} is {geom.get('type')!r}, expected LineString — "
                "the 110m coastline layout changed; re-check the cached file"
            )
        coords = [(float(lon), float(lat)) for lon, lat in geom["coordinates"]]
        if len(coords) >= 2:
            lines.append(coords)
    if not lines:
        raise BasemapError(f"{path}: no LineString features — empty coastline")
    return lines


def _to_px(lon: float, lat: float) -> tuple[float, float]:
    """Equirectangular (lon, lat) -> pixel coordinates on the WIDTH x HEIGHT canvas."""
    x = (lon + 180.0) / 360.0 * WIDTH
    y = (90.0 - lat) / 180.0 * HEIGHT
    return min(x, WIDTH - 1.0), min(y, HEIGHT - 1.0)


def draw_coastlines(
    rgb: np.ndarray,
    lines: list[list[tuple[float, float]]],
    colour: str = COASTLINE_COLOUR,
) -> np.ndarray:
    """Draw 1 px coastline polylines onto a styled (H, W, 3) uint8 image.

    Deterministic by construction: PIL's aliased 1 px line rasterisation over
    fixed integer geometry. Segments jumping more than 180 deg of longitude are
    split defensively (the cached 110m file has none) so a re-export of the
    source data can never smear a line across the whole map.
    """
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3) uint8 RGB, got {rgb.dtype} {rgb.shape}")
    if rgb.shape[:2] != (HEIGHT, WIDTH):
        raise ValueError(f"expected {HEIGHT}x{WIDTH} canvas, got {rgb.shape[:2]}")
    img = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(img)
    fill = tuple(int(v) for v in _hex_rgb(colour))
    for coords in lines:
        run: list[tuple[float, float]] = [_to_px(*coords[0])]
        for (lon0, _), (lon1, lat1) in zip(coords, coords[1:]):
            if abs(lon1 - lon0) > 180.0:        # antimeridian jump: break the polyline
                if len(run) >= 2:
                    draw.line(run, fill=fill, width=1)
                run = []
            run.append(_to_px(lon1, lat1))
        if len(run) >= 2:
            draw.line(run, fill=fill, width=1)
    return np.asarray(img, dtype=np.uint8)


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
    coastline_path: pathlib.Path = DEFAULT_COASTLINE,
) -> int:
    """Stitch -> style -> coastlines -> WebP q90. Returns the byte size written.

    Matches ramps.bake's discipline: the image is encoded twice in memory and
    the bake fails loudly if the two encodes differ, so non-determinism can
    never land silently.
    """
    styled = style_dark(stitch(tiles_root))
    styled = draw_coastlines(styled, load_coastlines(coastline_path))
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
    ap.add_argument("--coastline", type=pathlib.Path, default=DEFAULT_COASTLINE)
    args = ap.parse_args(argv)

    size = bake(args.tiles, args.out, args.quality, args.coastline)
    digest = hashlib.sha256(args.out.read_bytes()).hexdigest()
    print(f"baked {args.out} — {WIDTH}x{HEIGHT} WebP q{args.quality}, "
          f"{size:,} B, sha256 {digest}")


if __name__ == "__main__":
    main()
