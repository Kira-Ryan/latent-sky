"""The publisher's catalogue logic, tested without AWS.

    cd infra/daily && python -m pytest -q test_lambda_publish.py
"""

import os
import sys

import pytest

os.environ.setdefault("DATA_BUCKET", "x")
os.environ.setdefault("SITE_BUCKET", "y")
os.environ.setdefault("DISTRIBUTION_ID", "Z")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lambda_publish as lp  # noqa: E402


def manifest(init="2026-09-03T12:00:00Z", hero=True):
    return {
        "run": {"id": "daily-2026-09-03", "init": init},
        "frames": [init],
        "layers": {"a": {"kind": "hero-fine" if hero else "global"}},
    }


CURATED = {
    "schemaVersion": 1,
    "events": [
        {"id": "public-era5-gaemi-week-2024", "title": "Global", "subtitle": "ERA5", "manifest": "manifest.json",
         "kind": "global-only", "region": "global", "hasHero": False, "default": True},
        {"id": "us-dixie-2025", "title": "Central US", "subtitle": "StormCast", "manifest": "dixie/manifest.json",
         "kind": "hero", "region": "conus", "hasHero": True, "default": False},
    ],
}


def test_entry_reads_capabilities_from_the_manifest():
    e = lp.daily_entry("2026-09-03", manifest(), verified=False)
    assert e["id"] == "daily-2026-09-03" and e["manifest"] == "daily/2026-09-03/manifest.json"
    assert e["hasHero"] is True and e["kind"] == "hero" and e["region"] == "conus"
    assert e["title"] == "Central US — live run, 12Z 3 Sep 2026"
    assert "tomorrow" in e["subtitle"]
    assert "verified" in lp.daily_entry("2026-09-03", manifest(), verified=True)["subtitle"]
    assert lp.daily_entry("2026-09-03", manifest(hero=False), verified=False)["kind"] == "global-only"


def test_newest_daily_leads_and_is_the_only_default():
    c1 = lp.merge_catalogue(CURATED, lp.daily_entry("2026-09-03", manifest(), False))
    c2 = lp.merge_catalogue(c1, lp.daily_entry("2026-09-04", manifest("2026-09-04T12:00:00Z"), False))
    ids = [e["id"] for e in c2["events"]]
    assert ids == ["daily-2026-09-04", "daily-2026-09-03", "public-era5-gaemi-week-2024", "us-dixie-2025"]
    assert [e["default"] for e in c2["events"]] == [True, False, False, False]


def test_refreshing_a_day_replaces_it_in_place():
    c1 = lp.merge_catalogue(CURATED, lp.daily_entry("2026-09-03", manifest(), False))
    c2 = lp.merge_catalogue(c1, lp.daily_entry("2026-09-03", manifest(), True))
    dailies = [e for e in c2["events"] if e["id"].startswith("daily-")]
    assert len(dailies) == 1 and "verified against" in dailies[0]["subtitle"]


def test_window_rolls_off_the_oldest():
    cat = CURATED
    for d in range(1, 10):
        cat = lp.merge_catalogue(cat, lp.daily_entry(f"2026-09-{d:02d}", manifest(f"2026-09-{d:02d}T12:00:00Z"), False), keep=7)
    dailies = [e["id"] for e in cat["events"] if e["id"].startswith("daily-")]
    assert dailies == [f"daily-2026-09-{d:02d}" for d in range(9, 2, -1)]
    assert lp.dropped_dailies(cat, lp.merge_catalogue(cat, lp.daily_entry("2026-09-10", manifest("2026-09-10T12:00:00Z"), False), keep=7)) == ["daily-2026-09-03"]


def test_catalogue_rules_still_hold():
    bad = {"events": CURATED["events"] + [dict(CURATED["events"][1], id="dup-manifest")]}
    with pytest.raises(ValueError, match="share a manifest"):
        lp.merge_catalogue(bad, lp.daily_entry("2026-09-03", manifest(), False))
