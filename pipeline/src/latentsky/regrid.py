"""Curvilinear (2-D lat/lon) -> equirectangular nearest-neighbour regrid — §3.4, §5.4.

CorrDiff's output rides a rotated WRF Lambert grid whose curvature misplaces
features by up to 37 km if treated as a plain lat/lon box. Cesium's ImageryLayer
requires equirectangular imagery, so this stage is mandatory — and, as a side
effect, publishing on a grid WE define means the package's CC BY-NC-ND XLAT/XLONG
arrays are never redistributed (§3.10).

Nearest-neighbour, never bilinear: bilinear smooths away exactly the generated
fine structure the hero exists to demonstrate.

Target grid rule (reproduces §5.4's measured table, e.g. 579x566 at 1.25x):
    step_km   = native mean great-circle cell size / oversample
    width_px  = ceil(lon_span_deg * km_per_deg * cos(mid_lat) / step_km)
    height_px = ceil(lat_span_deg * km_per_deg / step_km)
with km_per_deg = 2*pi*R/360, R = 6371.0 km (great-circle, matching Probe 2's
measured 2.0684 km native step).

Nearest lookup runs on 3-D unit-sphere chords via scipy cKDTree, so it is exact
on the sphere and immune to longitude-wrap artefacts. Target pixels farther than
`max_dist_km` from any source cell (default 0.75x the native step — just above
the half-cell-diagonal 0.707x reachable inside the grid) are masked invalid and
must be rendered fully transparent, because the rotated grid's footprint does
not fill its own bounding box.
"""

from __future__ import annotations

import dataclasses
import math

import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_KM = 6371.0
KM_PER_DEG = 2.0 * math.pi * EARTH_RADIUS_KM / 360.0
DEFAULT_OVERSAMPLE = 1.25


@dataclasses.dataclass(frozen=True)
class TargetGrid:
    """A regular equirectangular pixel grid. rect is [west, south, east, north] degrees.

    Row 0 is the NORTHERNMOST row (image convention). Pixel (row r, col c) is
    centred at:
        lon = west + (c + 0.5) * (east - west) / width
        lat = north - (r + 0.5) * (north - south) / height
    """

    west: float
    south: float
    east: float
    north: float
    width: int
    height: int

    def __post_init__(self) -> None:
        if not (self.east > self.west and self.north > self.south):
            raise ValueError(f"degenerate rect: {self.rect}")
        if self.width < 1 or self.height < 1:
            raise ValueError(f"degenerate size: {self.width}x{self.height}")

    @property
    def rect(self) -> list[float]:
        return [self.west, self.south, self.east, self.north]

    @property
    def size(self) -> list[int]:
        return [self.width, self.height]

    def lon_centres(self) -> np.ndarray:
        return self.west + (np.arange(self.width) + 0.5) * (self.east - self.west) / self.width

    def lat_centres(self) -> np.ndarray:
        return self.north - (np.arange(self.height) + 0.5) * (self.north - self.south) / self.height


def native_step_km(lat: np.ndarray, lon: np.ndarray) -> float:
    """Mean great-circle spacing between adjacent cells, both axes (Probe 2's metric)."""
    if lat.ndim != 2 or lat.shape != lon.shape:
        raise ValueError(f"expected matching 2-D lat/lon, got {lat.shape} vs {lon.shape}")
    dx = _great_circle_km(lat[:, :-1], lon[:, :-1], lat[:, 1:], lon[:, 1:])
    dy = _great_circle_km(lat[:-1, :], lon[:-1, :], lat[1:, :], lon[1:, :])
    return float((dx.mean() + dy.mean()) / 2.0)


def _great_circle_km(lat1, lon1, lat2, lon2) -> np.ndarray:
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp, dl = p2 - p1, np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dp / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(a))


def grid_dims(
    west: float, south: float, east: float, north: float,
    native_step: float, oversample: float,
) -> tuple[int, int]:
    """(width, height) pixels for a bbox at native_step/oversample km per pixel.

    ceil, not round: the pixel step must never be coarser than the target step,
    or data is silently discarded (the mistake §5.4 records and corrects).
    """
    step = native_step / oversample
    mid_lat = math.radians((south + north) / 2.0)
    width_km = (east - west) * KM_PER_DEG * math.cos(mid_lat)
    height_km = (north - south) * KM_PER_DEG
    return math.ceil(width_km / step), math.ceil(height_km / step)


def target_from_bbox(
    lat: np.ndarray, lon: np.ndarray, oversample: float = DEFAULT_OVERSAMPLE
) -> TargetGrid:
    """The §5.4 target: the source's bounding box at 1.25x native oversampling.

    On the real 448x448 CWB grid this yields 579x566 — the measured §5.4 figure.
    """
    west, east = float(lon.min()), float(lon.max())
    south, north = float(lat.min()), float(lat.max())
    width, height = grid_dims(west, south, east, north, native_step_km(lat, lon), oversample)
    return TargetGrid(west=west, south=south, east=east, north=north, width=width, height=height)


@dataclasses.dataclass(frozen=True)
class NearestIndex:
    """A reusable source->target mapping: build once, apply to every field and frame."""

    grid: TargetGrid
    flat_index: np.ndarray  # (height, width) int64 into the flattened source arrays
    valid: np.ndarray       # (height, width) bool; False -> outside the source footprint

    def apply(self, field: np.ndarray) -> np.ndarray:
        """Regrid one 2-D source field. Invalid pixels carry NaN — the encoder must
        turn them fully transparent, never paint them."""
        if field.ndim != 2:
            raise ValueError(f"expected a 2-D field, got shape {field.shape}")
        out = np.asarray(field, dtype=np.float64).reshape(-1)[self.flat_index]
        out[~self.valid] = np.nan
        return out


def _to_unit_sphere_km(lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    """(N, 3) Cartesian coordinates in km, so KD-tree distances are chord km."""
    lat, lon = np.radians(np.asarray(lat_deg, np.float64)), np.radians(np.asarray(lon_deg, np.float64))
    return np.stack(
        [
            EARTH_RADIUS_KM * np.cos(lat) * np.cos(lon),
            EARTH_RADIUS_KM * np.cos(lat) * np.sin(lon),
            EARTH_RADIUS_KM * np.sin(lat),
        ],
        axis=-1,
    ).reshape(-1, 3)


def build_index(
    src_lat: np.ndarray,
    src_lon: np.ndarray,
    grid: TargetGrid,
    max_dist_km: float | None = None,
) -> NearestIndex:
    """Nearest-neighbour mapping from a curvilinear source grid onto `grid`.

    max_dist_km defaults to 0.75x the source's native step: any pixel whose
    nearest source cell is farther than that lies outside the grid footprint
    (interior pixels are always within the half-cell diagonal, ~0.707x).
    """
    if src_lat.ndim != 2 or src_lat.shape != src_lon.shape:
        raise ValueError(f"expected matching 2-D lat/lon, got {src_lat.shape} vs {src_lon.shape}")
    if max_dist_km is None:
        max_dist_km = 0.75 * native_step_km(src_lat, src_lon)

    tree = cKDTree(_to_unit_sphere_km(src_lat, src_lon))

    tgt_lon, tgt_lat = np.meshgrid(grid.lon_centres(), grid.lat_centres())
    dist, idx = tree.query(_to_unit_sphere_km(tgt_lat, tgt_lon), k=1)

    # dist is a 3-D chord; convert the great-circle threshold to its chord equivalent.
    chord_limit = 2.0 * EARTH_RADIUS_KM * math.sin(max_dist_km / (2.0 * EARTH_RADIUS_KM))
    valid = (dist <= chord_limit).reshape(grid.height, grid.width)
    return NearestIndex(
        grid=grid,
        flat_index=idx.reshape(grid.height, grid.width),
        valid=valid,
    )
