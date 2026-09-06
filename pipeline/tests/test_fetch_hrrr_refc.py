"""The HRRR index parse and the archive naming, without the network."""

import pathlib
import sys
from datetime import datetime

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import fetch_hrrr_refc as fh  # noqa: E402

IDX = """\
1:0:d=2026090512:REFC:entire atmosphere:6 hour fcst:
2:1234567:d=2026090512:RETOP:cloud top:6 hour fcst:
3:2345678:d=2026090512:VIL:entire atmosphere:6 hour fcst:
4:3456789:d=2026090512:VIS:surface:6 hour fcst:
"""


def test_the_composite_reflectivity_message_is_found_by_byte_range():
    assert fh.parse_idx(IDX) == (0, 1234566)


def test_a_later_message_ends_at_the_next_offset_and_the_last_runs_to_eof():
    assert fh.parse_idx(IDX, "VIL", "entire atmosphere") == (2345678, 3456788)
    assert fh.parse_idx(IDX, "VIS", "surface") == (3456789, None)


def test_a_missing_variable_is_an_error_not_a_guess():
    with pytest.raises(LookupError):
        fh.parse_idx(IDX, "REFD", "1000 m above ground")


def test_archive_key_follows_noaa_naming():
    assert fh.cycle_key(datetime(2026, 9, 5, 12), 6) == "hrrr.20260905/conus/hrrr.t12z.wrfsfcf06.grib2"
    assert fh.cycle_key(datetime(2025, 3, 14, 18), 0) == "hrrr.20250314/conus/hrrr.t18z.wrfsfcf00.grib2"
