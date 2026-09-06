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


class GuardedS3:
    """S3 with the error classes the publisher must tell apart."""

    class exceptions:
        class ClientError(Exception):
            def __init__(self, code):
                super().__init__(code)
                self.response = {"Error": {"Code": code}}

        class NoSuchKey(Exception):
            pass

    def __init__(self, site, data, index_error=None):
        self.site, self.data, self.index_error, self.puts = site, data, index_error, []

    def _store(self, bucket):
        return self.site if bucket == lp.SITE_BUCKET else self.data

    def get_object(self, Bucket, Key):
        import io
        if Bucket == lp.SITE_BUCKET and Key == "verification/index.json" and self.index_error is not None:
            raise self.index_error
        store = self._store(Bucket)
        if Key not in store:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": io.BytesIO(store[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.puts.append(Key)
        self._store(Bucket)[Key] = Body if isinstance(Body, bytes) else Body.encode()

    def head_object(self, Bucket, Key):
        if Key not in self._store(Bucket):
            raise self.exceptions.ClientError("404")
        return {}


def _wire(monkeypatch, fake):
    import json as _json
    monkeypatch.setattr(lp, "s3", fake)
    monkeypatch.setattr(lp, "invalidate", lambda paths: "INV")
    monkeypatch.setattr(lp.time, "sleep", lambda s: None)
    return _json


def _record_with(*ids):
    import json as _json
    return _json.dumps({"schemaVersion": 1, "runs": [
        {"id": i, "title": i, "init": "2026-09-01T12:00:00Z", "scored": True, "liveUrl": None,
         "members": 1, "reportUrl": f"/verification/{i}.html", "headline": None} for i in ids]}).encode()


def test_a_transient_record_read_failure_never_overwrites_it(monkeypatch):
    """The review's finding: any read failure was treated as 'no history', the
    record was rewritten with one row, and the publish reported success."""
    import json as _json
    fake = GuardedS3(
        {"data/web/catalogue.json": _json.dumps(CURATED).encode(),
         "verification/index.json": _record_with("daily-2026-09-01")},
        {"daily/2026-09-04/site.tar.gz": _fake_site_tar("daily-2026-09-04")},
        index_error=GuardedS3.exceptions.ClientError("AccessDenied"),
    )
    _wire(monkeypatch, fake)
    with pytest.raises(GuardedS3.exceptions.ClientError):
        lp.publish_site("2026-09-04", "daily/2026-09-04/site.tar.gz", verified=False)
    assert "verification/index.json" not in fake.puts, "the record was overwritten after a failed read"
    assert _json.loads(fake.site["verification/index.json"])["runs"][0]["id"] == "daily-2026-09-01"


def test_a_definitely_missing_record_is_started(monkeypatch):
    import json as _json
    fake = GuardedS3({"data/web/catalogue.json": _json.dumps(CURATED).encode()},
                     {"daily/2026-09-04/site.tar.gz": _fake_site_tar("daily-2026-09-04")})
    _wire(monkeypatch, fake)
    lp.publish_site("2026-09-04", "daily/2026-09-04/site.tar.gz", verified=False)
    assert [r["id"] for r in _json.loads(fake.site["verification/index.json"])["runs"]] == ["daily-2026-09-04"]


def test_an_unscored_tree_cannot_overwrite_a_scored_day(monkeypatch):
    """A replayed or re-uploaded plain site tar after the verified one would
    replace the radar layer and the report link while every marker said scored."""
    import json as _json
    fake = GuardedS3({"data/web/catalogue.json": _json.dumps(CURATED).encode(),
                      "verification/index.json": _record_with("daily-2026-09-04")},
                     {"daily/2026-09-04/site.tar.gz": _fake_site_tar("daily-2026-09-04"),
                      "daily/2026-09-04/scored.json": b"{}"})
    _wire(monkeypatch, fake)
    with pytest.raises(RuntimeError, match="already scored"):
        lp.publish_site("2026-09-04", "daily/2026-09-04/site.tar.gz", verified=False)
    assert fake.puts == [], "something was uploaded before the refusal"


def test_a_scored_publish_without_its_results_fails(monkeypatch):
    """pod_daily.sh uploads fss.json before the verified tar; if it is not there
    the ordering broke, and a scored row with no figure must not be written."""
    import json as _json
    fake = GuardedS3({"data/web/catalogue.json": _json.dumps(CURATED).encode(),
                      "verification/index.json": _record_with("daily-2026-09-01")},
                     {"daily/2026-09-04/site-verified.tar.gz": _fake_site_tar("daily-2026-09-04")})
    _wire(monkeypatch, fake)
    with pytest.raises(GuardedS3.exceptions.NoSuchKey):
        lp.publish_site("2026-09-04", "daily/2026-09-04/site-verified.tar.gz", verified=True)
    assert "verification/index.json" not in fake.puts
