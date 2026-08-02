"""Global-layer geometry on synthetic fields where the correct answer is known.

No network and no data/dev/: the ERA5 grid is reconstructed synthetically
(latitude 90..-90 x 0.25 descending, longitude 0..359.75 ascending — the layout
fetch_era5 asserts on the real ARCO store). Quarter-degree values are exactly
representable in float64, so every assertion here is exact equality, not
approximate.
"""

from __future__ import annotations

import numpy as np
import pytest

from latentsky import encode_global


def _era5_axes() -> tuple[np.ndarray, np.ndarray]:
    lat = 90.0 - 0.25 * np.arange(721)
    lon = 0.25 * np.arange(1440)
    return lat, lon


def test_roll_moves_the_dateline_to_column_zero():
    """A field whose value IS its longitude must come back ordered -180..179.75."""
    _, lon = _era5_axes()
    field = np.broadcast_to(lon, (3, lon.size)).copy()
    rolled, rolled_lon = encode_global.roll_to_180(field, lon)

    assert rolled_lon[0] == -180.0 and rolled_lon[-1] == 179.75
    assert np.all(np.diff(rolled_lon) > 0), "rolled longitudes must be strictly increasing"
    # value at each rolled column is that column's longitude expressed in 0..360
    assert np.array_equal(rolled, np.broadcast_to(np.mod(rolled_lon, 360.0), rolled.shape))
    # the prime meridian (original column 0) lands exactly at the centre column
    assert rolled[0, 720] == 0.0
    # the dateline (original 180.0 column) lands at column 0
    assert rolled[0, 0] == 180.0


def test_roll_rejects_grids_it_cannot_roll_exactly():
    _, lon = _era5_axes()
    field = np.zeros((2, lon.size))
    no_dateline = 0.7 * np.arange(514)                   # 0..359.1, no exact 180.0 column
    with pytest.raises(ValueError, match="180.0"):
        encode_global.roll_to_180(np.zeros((2, 514)), no_dateline)
    with pytest.raises(ValueError, match="ascending"):
        encode_global.roll_to_180(field, lon[::-1])      # descending
    with pytest.raises(ValueError, match="ascending"):
        encode_global.roll_to_180(field, lon - 180.0)    # already -180..180
    with pytest.raises(ValueError, match="does not match"):
        encode_global.roll_to_180(np.zeros((2, 10)), lon)


def test_downsample_is_the_exact_even_subgrid():
    """out[r, c] must be source[2r, 2c] — both poles retained, nothing averaged."""
    field = (10_000.0 * np.arange(721)[:, None]) + np.arange(1440)[None, :]
    out = encode_global.downsample_half(field)
    assert out.shape == (361, 720)
    expected = (10_000.0 * (2 * np.arange(361))[:, None]) + (2 * np.arange(720))[None, :]
    assert np.array_equal(out, expected)
    assert out[0, 0] == 0.0                  # row 0 = source row 0 = 90 N
    assert out[-1, 0] == 10_000.0 * 720      # last row = source row 720 = 90 S


def test_downsample_rejects_wrong_shapes():
    with pytest.raises(ValueError, match="expected trailing"):
        encode_global.downsample_half(np.zeros((720, 1440)))
    with pytest.raises(ValueError, match="expected trailing"):
        encode_global.downsample_half(np.zeros((721, 1441)))


def test_prepare_global_end_to_end_on_a_known_field():
    """field(lat, lon) = 1000*lat + signed_lon: after the full chain, pixel (r, c)
    must equal 1000*(90 - 0.5r) + (-180 + 0.5c) EXACTLY — north at row 0,
    -180..180 west->east, 0.5 deg steps."""
    lat, lon = _era5_axes()
    signed_lon = np.mod(lon + 180.0, 360.0) - 180.0
    field = 1000.0 * lat[:, None] + signed_lon[None, :]

    out = encode_global.prepare_global(field, lat, lon)

    assert out.shape == (361, 720)
    exp_lat = 90.0 - 0.5 * np.arange(361)
    exp_lon = -180.0 + 0.5 * np.arange(720)
    assert np.array_equal(out, 1000.0 * exp_lat[:, None] + exp_lon[None, :])


def test_prepare_global_rejects_ascending_latitude():
    lat, lon = _era5_axes()
    field = np.zeros(encode_global.ERA5_SHAPE)
    with pytest.raises(ValueError, match="descending"):
        encode_global.prepare_global(field, lat[::-1], lon)
    with pytest.raises(ValueError, match="expected"):
        encode_global.prepare_global(np.zeros((10, 10)), lat, lon)


def test_global_grid_is_the_s8_contract():
    grid = encode_global.GLOBAL_GRID
    assert grid.size == [720, 361]
    assert grid.rect == [-180.0, -90.0, 180.0, 90.0]


def test_tree_sha256_is_deterministic_and_sensitive(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.bin").write_bytes(b"alpha")
    (tmp_path / "sub" / "b.bin").write_bytes(b"beta")
    first = encode_global.tree_sha256(tmp_path)
    assert first == encode_global.tree_sha256(tmp_path), "same tree must hash identically"
    (tmp_path / "sub" / "b.bin").write_bytes(b"betb")
    assert encode_global.tree_sha256(tmp_path) != first, "one changed byte must change the digest"
    with pytest.raises(NotADirectoryError):
        encode_global.tree_sha256(tmp_path / "a.bin")


def test_hero_records_survive_a_manifest_round_trip():
    entry = {
        "kind": "hero-fine", "variable": "wind10m", "label": "x", "units": "m/s",
        "rect": [1.0, 2.0, 3.0, 4.0], "size": [8, 8], "lut": "luts/wind10m.lut.png",
        "vmin": 0.0, "vmax": 55.0, "identity": "f" * 64, "frames": ["a.webp"],
        "pairWith": "other",
    }
    manifest = {"layers": {"wind10m-fine": entry, "skip-me": {**entry, "kind": "global"}}}
    records = encode_global.hero_records_from_manifest(manifest)
    assert [r.layer_id for r in records] == ["wind10m-fine"]
    rec = records[0]
    assert rec.identity == "f" * 64 and rec.pair_with == "other" and rec.kind == "hero-fine"
