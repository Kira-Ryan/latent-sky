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
