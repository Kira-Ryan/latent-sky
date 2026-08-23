"""fetch_era5's pure gates — no network, no data/dev/.

The fetch itself is exercised manually (it writes the gitignored raw cache);
what CI must hold is the §3.6 discipline: only timestamps old enough to be
FINAL ERA5 may ever be requested, because ERA5T inside the ~3-month window is
overwritten and byte-unstable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from latentsky import fetch_era5


def test_parse_utc_accepts_z_and_naive():
    assert fetch_era5.parse_utc("2021-02-02T00:00:00Z") == datetime(2021, 2, 2, tzinfo=timezone.utc)
    assert fetch_era5.parse_utc("2021-02-02T00:00:00") == datetime(2021, 2, 2, tzinfo=timezone.utc)


def test_parse_utc_rejects_offsets_and_garbage():
    with pytest.raises(fetch_era5.FetchError, match="offset"):
        fetch_era5.parse_utc("2021-02-02T00:00:00+08:00")
    with pytest.raises(fetch_era5.FetchError, match="unparseable"):
        fetch_era5.parse_utc("not-a-time")


def test_final_era5_gate_passes_the_dev_times():
    fetch_era5.check_final_era5([
        "2021-02-02T00:00:00Z", "2021-03-02T00:00:00Z", "2021-04-02T00:00:00Z",
        "2021-09-12T00:00:00Z", "2021-09-12T12:00:00Z",
    ])  # must not raise: 2021 is final ERA5, not ERA5T


def test_final_era5_gate_refuses_recent_times():
    """A timestamp inside the ERA5T window must be refused loudly (§3.6)."""
    recent = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%dT00:00:00Z")
    with pytest.raises(fetch_era5.FetchError, match="ERA5T"):
        fetch_era5.check_final_era5([recent])


def test_fetch_refuses_unsorted_or_duplicate_times(tmp_path):
    with pytest.raises(fetch_era5.FetchError, match="strictly increasing"):
        fetch_era5.fetch(["2021-03-02T00:00:00Z", "2021-02-02T00:00:00Z"], tmp_path / "x.npz")
    with pytest.raises(fetch_era5.FetchError, match="strictly increasing"):
        fetch_era5.fetch(["2021-02-02T00:00:00Z", "2021-02-02T00:00:00Z"], tmp_path / "x.npz")
    with pytest.raises(fetch_era5.FetchError, match="no timestamps"):
        fetch_era5.fetch([], tmp_path / "x.npz")


# ------------------------------------------------------------------ sequence mode

def test_times_from_range_builds_the_gaemi_week():
    times = fetch_era5.times_from_range("2024-07-22T00:00:00Z", "2024-07-29T18:00:00Z", 6)
    assert len(times) == 32
    assert times[0] == "2024-07-22T00:00:00Z"
    assert times[1] == "2024-07-22T06:00:00Z"
    assert times[-1] == "2024-07-29T18:00:00Z"
    assert times == sorted(times) and len(set(times)) == 32
    fetch_era5.check_final_era5(times)  # July 2024 is final ERA5 — must not raise


def test_times_from_range_at_12_hourly_is_the_public_subset():
    cache = fetch_era5.times_from_range("2024-07-22T00:00:00Z", "2024-07-29T18:00:00Z", 6)
    public = fetch_era5.times_from_range("2024-07-22T00:00:00Z", "2024-07-29T12:00:00Z", 12)
    assert len(public) == 16
    assert public == cache[::2], "12-hourly must be exactly every other 6-hourly step"


def test_times_from_range_rejects_ragged_and_inverted_ranges():
    with pytest.raises(fetch_era5.FetchError, match="does not divide"):
        fetch_era5.times_from_range("2024-07-22T00:00:00Z", "2024-07-22T07:00:00Z", 6)
    with pytest.raises(fetch_era5.FetchError, match="after start"):
        fetch_era5.times_from_range("2024-07-23T00:00:00Z", "2024-07-22T00:00:00Z", 6)
    with pytest.raises(fetch_era5.FetchError, match="positive"):
        fetch_era5.times_from_range("2024-07-22T00:00:00Z", "2024-07-23T00:00:00Z", 0)


def test_attribution_years_names_the_data_years():
    assert fetch_era5._attribution_years(["2024-07-22T00:00:00Z"]) == "2024"
    assert fetch_era5._attribution_years(
        ["2021-02-02T00:00:00Z", "2024-07-22T00:00:00Z"]
    ) == "2021–2024"


# ------------------------------------------------------------------ cache matching

def _write_cache(path, times, shape):
    import numpy as np
    arrays = {k: np.zeros((len(times), *shape), dtype=np.float32) for k in fetch_era5.VARIABLES}
    np.savez(path, **arrays,
             latitude=np.zeros(shape[0]), longitude=np.zeros(shape[1]),
             times=np.array(times), source=np.array("test"))


def test_cache_matches_is_grid_aware(tmp_path):
    """A half-degree cache must NOT satisfy a full-resolution request (and vice
    versa) — the shapes carry meaning, not just the timestamps."""
    times = ["2021-02-02T00:00:00Z", "2021-03-02T00:00:00Z"]
    half = tmp_path / "half.npz"
    _write_cache(half, times, fetch_era5.HALF_SHAPE)
    assert fetch_era5.cache_matches(half, times, fetch_era5.HALF_SHAPE)
    assert not fetch_era5.cache_matches(half, times, fetch_era5.ERA5_SHAPE)
    assert not fetch_era5.cache_matches(half, times[:1], fetch_era5.HALF_SHAPE)
    assert not fetch_era5.cache_matches(tmp_path / "absent.npz", times, fetch_era5.HALF_SHAPE)
