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
    assert e["title"] == "Central US — daily run, 12Z 3 Sep 2026"
    # Neither line may bake a claim that cannot retract itself: no "live" in a
    # title that outlives being newest, no "tomorrow" in a subtitle that a failed
    # scoring pass makes false. State only.
    assert e["subtitle"].endswith("not yet scored")
    assert "tomorrow" not in e["subtitle"] and "live" not in e["title"].lower()
    assert lp.daily_entry("2026-09-03", manifest(), verified=True)["subtitle"].endswith(
        "scored against MRMS radar"
    )
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
    assert len(dailies) == 1 and "scored against MRMS radar" in dailies[0]["subtitle"]


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


def test_index_row_is_created_unscored_and_upgraded_when_scored():
    """The record is of every run, not only the ones that scored well."""
    m = manifest()
    unscored = lp.index_row("2026-09-04", m, None, verified=False)
    assert unscored["scored"] is False and unscored["reportUrl"] is None and unscored["headline"] is None
    scored = lp.index_row("2026-09-04", m, {"headline": {"thresholdDbz": 40, "usefulScaleKm": None,
                                                         "usefulHours": 0, "scoredHours": 17,
                                                         "largestScaleKm": 98.2}}, verified=True)
    assert scored["scored"] is True
    assert scored["reportUrl"] == "/verification/daily-2026-09-04.html"
    assert scored["headline"]["usefulScaleKm"] is None, "a no-skill result must survive into the index"


def test_merge_index_replaces_the_same_day_and_orders_newest_first():
    idx = {"schemaVersion": 1, "runs": []}
    idx = lp.merge_index(idx, lp.index_row("2026-09-02", manifest("2026-09-02T12:00:00Z"), None, False))
    idx = lp.merge_index(idx, lp.index_row("2026-09-04", manifest("2026-09-04T12:00:00Z"), None, False))
    # the same day comes back scored
    idx = lp.merge_index(idx, lp.index_row("2026-09-02", manifest("2026-09-02T12:00:00Z"),
                                           {"headline": {"thresholdDbz": 40, "usefulScaleKm": 50.3,
                                                         "usefulHours": 3, "scoredHours": 17,
                                                         "largestScaleKm": 98.2}}, True))
    ids = [r["id"] for r in idx["runs"]]
    assert ids == ["daily-2026-09-04", "daily-2026-09-02"], ids
    assert sum(1 for r in idx["runs"] if r["id"] == "daily-2026-09-02") == 1
    assert idx["runs"][1]["scored"] is True and idx["runs"][1]["headline"]["usefulScaleKm"] == 50.3


def test_a_scored_row_must_name_its_report():
    bad = dict(lp.index_row("2026-09-04", manifest(), None, verified=True), reportUrl=None)
    with pytest.raises(ValueError, match="name its report"):
        lp.merge_index({"schemaVersion": 1, "runs": []}, bad)


def test_publishing_a_scored_run_actually_writes_the_record(monkeypatch):
    """The call-site test. Every merge_index test above passes with the call
    removed entirely, which would ship a scored run the index never lists."""
    import io, json as _json

    written, invalidated = {}, []
    site = {
        "data/web/catalogue.json": _json.dumps(CURATED).encode(),
        "verification/index.json": _json.dumps({"schemaVersion": 1, "runs": []}).encode(),
    }
    tar = _fake_site_tar("daily-2026-09-04")
    data = {
        "daily/2026-09-04/site-verified.tar.gz": tar,
        "daily/2026-09-04/fss.json": _json.dumps({"headline": {"thresholdDbz": 40, "usefulScaleKm": 98.2,
                                                               "usefulHours": 7, "scoredHours": 17,
                                                               "largestScaleKm": 98.2}}).encode(),
    }

    class FakeS3:
        def get_object(self, Bucket, Key):
            store = site if Bucket == lp.SITE_BUCKET else data
            return {"Body": io.BytesIO(store[Key])}

        def put_object(self, Bucket, Key, Body, **kw):
            written[Key] = Body
            if Bucket == lp.SITE_BUCKET:
                site[Key] = Body if isinstance(Body, bytes) else Body.encode()

    monkeypatch.setattr(lp, "s3", FakeS3())
    monkeypatch.setattr(lp, "invalidate", lambda paths: invalidated.extend(paths) or "INV")
    monkeypatch.setattr(lp.time, "sleep", lambda s: None)

    lp.publish_site("2026-09-04", "daily/2026-09-04/site-verified.tar.gz", verified=True)

    assert "verification/index.json" in written, "a scored publish did not touch the record"
    rec = _json.loads(site["verification/index.json"])
    row = next(r for r in rec["runs"] if r["id"] == "daily-2026-09-04")
    assert row["scored"] is True and row["headline"]["usefulScaleKm"] == 98.2
    assert "/verification/index.json" in invalidated, "the record was written but never invalidated"


def _fake_site_tar(event_id: str) -> bytes:
    """A minimal event tree: one manifest and one frame."""
    import io, tarfile, json as _json

    m = _json.dumps({"run": {"id": event_id, "init": "2026-09-04T12:00:00Z"},
                     "frames": ["2026-09-04T12:00:00Z"],
                     "layers": {"a": {"kind": "hero-fine"}}}).encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, body in ((f"{event_id}/manifest.json", m), (f"{event_id}/layers/a/000.webp", b"x")):
            info = tarfile.TarInfo(name); info.size = len(body)
            t.addfile(info, io.BytesIO(body))
    return buf.getvalue()
