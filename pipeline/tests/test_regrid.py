"""Regrid correctness on synthetic grids where the right answer is known — §5.4.

No test here touches data/dev/ (CC BY-NC-ND, local-only, absent in CI). The
bbox/step constants in test_grid_dims_reproduce_spec_table are published
measurements from Architecture.md §3.4/§5.4, not redistributed data.
"""

from __future__ import annotations

import numpy as np
import pytest

from latentsky import regrid


def _rotated_grid(n=24, m=20, angle_deg=12.0, centre_lat=23.5, centre_lon=121.0, step_deg=0.1):
    """A synthetic curvilinear grid: a regular local grid rotated about its centre.

    Rotation applied in the local tangent plane (x scaled by cos(lat)) so the
    great-circle cell size stays uniform — the same character as the WRF grid.
    """
    j, i = np.meshgrid(np.arange(m) - (m - 1) / 2, np.arange(n) - (n - 1) / 2, indexing="xy")
    theta = np.radians(angle_deg)
    x = (j * np.cos(theta) - i * np.sin(theta)) * step_deg
    y = (j * np.sin(theta) + i * np.cos(theta)) * step_deg
    lat = centre_lat + y
    lon = centre_lon + x / np.cos(np.radians(centre_lat))
    return lat, lon


def test_identity_on_regular_grid():
    """Source grid == target pixel centres -> every value must come back exactly."""
    lon_c = 120.0 + (np.arange(10) + 0.5) * 0.1
    lat_c = 25.0 - (np.arange(8) + 0.5) * 0.1  # row 0 north, matching TargetGrid
    lon2d, lat2d = np.meshgrid(lon_c, lat_c)
    grid = regrid.TargetGrid(west=120.0, south=24.2, east=121.0, north=25.0, width=10, height=8)
    index = regrid.build_index(lat2d, lon2d, grid)
    field = np.arange(80, dtype=np.float64).reshape(8, 10) * 3.5 - 7.0
    out = index.apply(field)
    assert index.valid.all()
    assert np.array_equal(out, field)


def test_nearest_matches_brute_force_on_rotated_grid():
    """The KD-tree answer must equal an exhaustive nearest-neighbour search."""
    lat, lon = _rotated_grid()
    grid = regrid.target_from_bbox(lat, lon, oversample=1.25)
    index = regrid.build_index(lat, lon, grid)

    src_xyz = regrid._to_unit_sphere_km(lat, lon)
    tgt_lon, tgt_lat = np.meshgrid(grid.lon_centres(), grid.lat_centres())
    tgt_xyz = regrid._to_unit_sphere_km(tgt_lat, tgt_lon)
    d2 = ((tgt_xyz[:, None, :] - src_xyz[None, :, :]) ** 2).sum(axis=2)
    brute = d2.argmin(axis=1).reshape(grid.height, grid.width)

    assert np.array_equal(index.flat_index, brute)


def test_rotated_grid_values_land_at_source_positions():
    """Sampling the target at each source cell's own lat/lon must return that cell's
    value — nearest-neighbour is exact at zero distance."""
    lat, lon = _rotated_grid()
    field = (np.arange(lat.size, dtype=np.float64) * 1.25).reshape(lat.shape)
    grid = regrid.target_from_bbox(lat, lon, oversample=2.0)
    index = regrid.build_index(lat, lon, grid)
    out = index.apply(field)

    # For a sample of source cells, find the target pixel containing that cell centre.
    for (r, c) in [(0, 0), (5, 7), (12, 3), (23, 19), (11, 10)]:
        col = int((lon[r, c] - grid.west) / (grid.east - grid.west) * grid.width)
        row = int((grid.north - lat[r, c]) / (grid.north - grid.south) * grid.height)
        col, row = min(col, grid.width - 1), min(row, grid.height - 1)
        if index.valid[row, col]:
            # the pixel's nearest source is either this cell or one whose value differs;
            # at 2x oversampling the containing pixel centre is within half a source cell,
            # so it must be this cell's value.
            assert out[row, col] == field[r, c]


def test_corners_outside_footprint_are_masked():
    """A rotated grid does not fill its own bbox: the bbox corners must be invalid,
    and applied fields must carry NaN there, never a smeared edge value."""
    lat, lon = _rotated_grid(angle_deg=25.0)
    grid = regrid.target_from_bbox(lat, lon)
    index = regrid.build_index(lat, lon, grid)
    assert not index.valid[0, 0]
    assert not index.valid[-1, -1]
    assert index.valid.any()
    out = index.apply(np.ones(lat.shape))
    assert np.isnan(out[0, 0]) and np.isnan(out[-1, -1])
    assert np.nanmax(out) == 1.0


def test_native_step_measures_construction():
    """The synthetic grid is built at 0.1 deg local spacing -> ~11.1 km cells."""
    lat, lon = _rotated_grid()
    step = regrid.native_step_km(lat, lon)
    assert step == pytest.approx(0.1 * regrid.KM_PER_DEG, rel=0.01)


def test_grid_dims_reproduce_spec_table():
    """§5.4's measured table from the published bbox and the 2.0684 km native step."""
    west, south, east, north = 116.1372, 19.5187, 125.5459, 27.9282
    step = 2.0684
    assert regrid.grid_dims(west, south, east, north, step, 1.25) == (579, 566)
    assert regrid.grid_dims(west, south, east, north, step, 1.50) == (695, 679)


def test_degenerate_inputs_raise():
    with pytest.raises(ValueError):
        regrid.TargetGrid(west=1.0, south=1.0, east=1.0, north=2.0, width=4, height=4)
    with pytest.raises(ValueError):
        regrid.TargetGrid(west=0.0, south=0.0, east=1.0, north=1.0, width=0, height=4)
    lat, lon = _rotated_grid()
    grid = regrid.target_from_bbox(lat, lon)
    index = regrid.build_index(lat, lon, grid)
    with pytest.raises(ValueError):
        index.apply(np.zeros((3, 3, 3)))
