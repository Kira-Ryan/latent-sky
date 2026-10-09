"""An hour with no radar is an undefined hour, not the end of the day.

On 7 Oct 2026 the MRMS archive had no file from 14:28 to 16:02 UTC. The
protocol always said a refused match refuses that hour; the code refused the
whole run, and eighteen hours that had radar went unscored for the sake of one
that did not. These pin the per-hour behaviour, end to end through the scorer.
"""

from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import fetch_mrms  # noqa: E402
import verify_fss  # noqa: E402

from test_identity_guards import SEP, write_hero, write_mrms  # noqa: E402


def plan(offsets):
    return [{"lead": i, "valid": f"2026-10-07T{12 + i:02d}:00:00Z", "key": f"k{i}", "offset_s": o}
            for i, o in enumerate(offsets)]


def test_one_missing_hour_is_set_aside_not_the_run():
    used, gone = fetch_mrms.classify(plan([37, 40, 41, -1885, 159, 40]))
    assert [p["lead"] for p in gone] == [3]
    assert len(used) == 5


def test_a_broken_archive_is_still_refused():
    """More than half the hours missing is not a gap but a broken archive."""
    with pytest.raises(SystemExit, match="broken"):
        fetch_mrms.classify(plan([40, 2000, 2000, 2000, 2000, 40]))


def test_exactly_half_missing_is_scored():
    used, gone = fetch_mrms.classify(plan([40, 2000, 40, 2000, 40, 2000]))
    assert len(gone) == 3 and len(used) == 3


def test_the_tolerance_boundary_is_inclusive():
    used, gone = fetch_mrms.classify(plan([300, -300, 301]))
    assert [p["lead"] for p in gone] == [2]


def _mrms_with_a_gap(path, valid, gap_lead):
    """write_mrms, then blank one hour exactly as fetch_mrms does for a gap."""
    write_mrms(path, valid)
    m = dict(np.load(path))
    m["refc_half_dbz"][gap_lead] = fetch_mrms.NO_COVERAGE_HALF_DBZ
    off = m["offset_s"].astype(float)
    off[gap_lead] = np.nan
    m["offset_s"] = off
    m["missing"] = np.array([i == gap_lead for i in range(len(valid))])
    np.savez_compressed(path, **m)


def test_an_hour_with_no_radar_is_undefined_and_the_rest_are_scored(tmp_path):
    leads = [0, 1, 2, 3, 4, 5]
    hero = write_hero(tmp_path / "sep_hero.zarr", SEP, leads, seed=0)
    valid = [f"2026-09-05T{12 + h:02d}:00:00Z" for h in leads]
    mrms = tmp_path / "mrms.npz"
    _mrms_with_a_gap(mrms, valid, gap_lead=3)
    out = tmp_path / "fss.json"
    verify_fss.main(["--hero-zarr", str(hero), "--mrms", str(mrms), "--out", str(out),
                     "--event-id", "daily-2026-09-05"])
    r = json.loads(out.read_text(encoding="utf-8"))

    assert r["event"]["mrms_missing"] == ["2026-09-05T15:00:00Z"]
    assert math.isfinite(r["event"]["mrms_worst_offset_s"]), "the worst offset must be of files actually used"

    gap = r["leads"][3]
    assert gap["no_radar"] is True and gap["valid_cells"] == 0
    assert all(not r["leads"][i]["no_radar"] for i in (0, 1, 2, 4, 5))

    h = r["headline"]
    # Post-spin-up hours are leads 2..5: three scored, the gap one undefined.
    assert h["scoredHours"] == 3 and h["undefinedHours"] == 1
    assert h["status"] in ("useful", "below-line")


def test_an_hour_with_no_radar_raises_no_numpy_warnings(tmp_path):
    """The arithmetic of an empty hour is NaN by construction; its warnings would
    send a reader of the pod log chasing a non-bug."""
    import warnings
    leads = [0, 1, 2, 3]
    hero = write_hero(tmp_path / "sep_hero.zarr", SEP, leads, seed=0)
    valid = [f"2026-09-05T{12 + h:02d}:00:00Z" for h in leads]
    mrms = tmp_path / "mrms.npz"
    _mrms_with_a_gap(mrms, valid, gap_lead=2)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        verify_fss.main(["--hero-zarr", str(hero), "--mrms", str(mrms), "--out", str(tmp_path / "fss.json"),
                         "--event-id", "daily-2026-09-05"])
