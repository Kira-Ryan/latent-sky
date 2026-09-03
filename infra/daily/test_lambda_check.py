"""The deadman's two jobs: bound the spend, and never report a clean day that was not.

    cd infra/daily && python -m pytest -q test_lambda_check.py
"""

import datetime as dt
import os
import sys

import pytest

os.environ.setdefault("DATA_BUCKET", "data-bucket")
os.environ.setdefault("SITE_BUCKET", "site-bucket")
os.environ.setdefault("TOPIC_ARN", "arn:aws:sns:us-east-1:1:topic")
os.environ.setdefault("MAX_POD_MINUTES", "45")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lambda_check as lc  # noqa: E402

NOW = dt.datetime(2026, 9, 3, 20, 35, tzinfo=dt.timezone.utc)


def pod(name="latentsky-daily-2026-09-03", pid="p1", started="2026-09-03 19:00:00.000 +0000 UTC"):
    return {"id": pid, "name": name, "lastStartedAt": started, "costPerHr": 2.09}


def test_runpods_timestamp_format_parses():
    """The format RunPod actually returns. If this stops parsing, every pod looks
    stale and gets killed mid-run."""
    assert lc.pod_age_minutes(pod(started="2026-09-03 19:35:00.000 +0000 UTC"), NOW) == pytest.approx(60.0)
    assert lc.pod_age_minutes(pod(started="2026-09-03T19:35:00Z"), NOW) == pytest.approx(60.0)
    assert lc.pod_age_minutes(pod(started="not a time"), NOW) is None


def test_over_age_pods_are_killed_and_young_ones_left(monkeypatch):
    calls = []
    pods = [pod(pid="old", started="2026-09-03 19:00:00.000 +0000 UTC"),   # 95 min
            pod(pid="young", started="2026-09-03 20:20:00.000 +0000 UTC")]  # 15 min
    def fake(method, path):
        calls.append((method, path))
        if method == "GET":
            return [p for p in pods if not any(c == ("DELETE", f"/pods/{p['id']}") for c in calls)]
        return None
    monkeypatch.setattr(lc, "runpod", fake)
    killed, problems = lc.reap(NOW)
    assert ("DELETE", "/pods/old") in calls
    assert ("DELETE", "/pods/young") not in calls
    assert len(killed) == 1 and problems == []


def test_a_pod_that_survives_its_termination_is_reported(monkeypatch):
    """The bug: a DELETE was assumed to have worked, so a pod that kept running
    was reported as terminated and billed on."""
    def fake(method, path):
        if method == "GET":
            return [pod(pid="stubborn", started="2026-09-03 18:00:00.000 +0000 UTC")]
        return None       # DELETE "succeeds" but the pod never goes away
    monkeypatch.setattr(lc, "runpod", fake)
    killed, problems = lc.reap(NOW)
    assert killed and any("STILL RUNNING" in p for p in problems)


def test_an_unreadable_start_time_is_killed_not_ignored(monkeypatch):
    seen = []
    def fake(method, path):
        seen.append((method, path))
        return [] if method == "GET" and seen.count(("GET", "/pods")) > 1 else (
            [pod(pid="weird", started="???")] if method == "GET" else None)
    monkeypatch.setattr(lc, "runpod", fake)
    killed, problems = lc.reap(NOW)
    assert ("DELETE", "/pods/weird") in seen
    assert any("unparseable" in p for p in problems)


def test_runpod_raises_rather_than_returning_nothing(monkeypatch):
    """The bug this pins: runpod() swallowed transport errors and returned None,
    so a call that never happened was indistinguishable from "no pods running".

    This exercises the REAL runpod() against a failing socket — stubbing runpod
    itself would test the stub, and the swallow could come back unnoticed.
    """
    monkeypatch.setattr(lc.ssm, "get_parameter",
                        lambda **kw: {"Parameter": {"Value": "key"}}, raising=False)
    monkeypatch.setattr(lc.urllib.request, "urlopen",
                        lambda req, timeout=None: (_ for _ in ()).throw(OSError("connection reset")))
    with pytest.raises(OSError):
        lc.runpod("GET", "/pods")
    # ...and the list helper must not paper over it either.
    with pytest.raises(OSError):
        lc.list_daily_pods()


def test_a_runpod_outage_never_reports_a_clean_day(monkeypatch):
    """The bug: a failed GET /pods returned an empty list, so a day with a pod
    still billing was reported clean."""
    monkeypatch.setattr(lc, "runpod", lambda m, p: (_ for _ in ()).throw(OSError("connection reset")))
    monkeypatch.setattr(lc, "audit_day", lambda d, p: [])
    sent = []
    monkeypatch.setattr(lc, "publish", lambda s, m: sent.append((s, m)))
    out = lc.handler({"mode": "report", "date": "2026-09-03"}, None)
    assert out["status"] == "alerted"
    assert any("COULD NOT REACH RUNPOD" in p for p in out["problems"])
    assert sent, "the alert was never published"


def test_the_reaper_does_not_email_on_every_failed_poll(monkeypatch):
    """96 identical emails during a RunPod outage would train the reader to ignore
    the one that matters. The daily report carries it instead."""
    monkeypatch.setattr(lc, "runpod", lambda m, p: (_ for _ in ()).throw(OSError("connection reset")))
    sent = []
    monkeypatch.setattr(lc, "publish", lambda s, m: sent.append(s))
    out = lc.handler({"mode": "reap", "date": "2026-09-03"}, None)
    assert out["status"] == "clean" and sent == []


def test_the_audits_own_failure_is_itself_an_alert(monkeypatch):
    monkeypatch.setattr(lc, "runpod", lambda m, p: [])
    monkeypatch.setattr(lc, "audit_day", lambda d, p: (_ for _ in ()).throw(RuntimeError("s3 exploded")))
    sent = []
    monkeypatch.setattr(lc, "publish", lambda s, m: sent.append((s, m)))
    out = lc.handler({"mode": "report", "date": "2026-09-03"}, None)
    assert out["status"] == "alerted" and any("audit itself failed" in p for p in out["problems"])


def test_findings_survive_a_later_failure(monkeypatch):
    """Reaping happens first and its findings are kept even if the audit dies."""
    def fake(method, path):
        if method == "GET":
            return [pod(pid="old", started="2026-09-03 18:00:00.000 +0000 UTC")]
        return None
    monkeypatch.setattr(lc, "runpod", fake)
    monkeypatch.setattr(lc, "audit_day", lambda d, p: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(lc, "publish", lambda s, m: None)
    out = lc.handler({"mode": "report", "date": "2026-09-03"}, None)
    assert any("Terminated pods" in p for p in out["problems"])
    assert any("audit itself failed" in p for p in out["problems"])


class FakeS3:
    class exceptions:
        class ClientError(Exception):
            def __init__(self, code):
                super().__init__(code)
                self.response = {"Error": {"Code": code}}

    def __init__(self, objects):
        self.objects = objects

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.ClientError("404")
        return {}

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[Key])}


def test_a_day_that_never_launched_is_alerted(monkeypatch):
    monkeypatch.setattr(lc, "s3", FakeS3({}))
    problems = lc.audit_day("2026-09-03", "2026-09-02")
    assert len(problems) == 1 and "NO LAUNCH" in problems[0]


def test_a_launched_day_with_no_site_tar_is_alerted(monkeypatch):
    import json
    monkeypatch.setattr(lc, "s3", FakeS3({
        "daily/2026-09-03/launched.json": json.dumps({"state": "launched", "pod_id": "p", "launched_at": "t"}).encode(),
        "daily/2026-09-03/run.log": b"+ set -x\nFATAL: HRRR channels are zero\n",
    }))
    problems = lc.audit_day("2026-09-03", "2026-09-02")
    assert any("no site tar" in p and "FATAL" in p for p in problems)


def test_a_dead_report_link_is_alerted(monkeypatch):
    import json
    monkeypatch.setattr(lc, "s3", FakeS3({
        "daily/2026-09-03/launched.json": json.dumps({"state": "launched", "pod_id": "p", "scores_prev": True}).encode(),
        "daily/2026-09-03/site.tar.gz": b"x",
        "daily/2026-09-03/published.json": b"{}",
        "daily/2026-09-02/scored.json": b"{}",
    }))
    monkeypatch.setattr(lc, "url_ok", lambda u: False)
    problems = lc.audit_day("2026-09-03", "2026-09-02")
    assert any("verification page is not reachable" in p for p in problems)


def test_a_clean_day_says_nothing(monkeypatch):
    import json
    monkeypatch.setattr(lc, "s3", FakeS3({
        "daily/2026-09-03/launched.json": json.dumps({"state": "launched", "pod_id": "p", "scores_prev": True}).encode(),
        "daily/2026-09-03/site.tar.gz": b"x",
        "daily/2026-09-03/published.json": b"{}",
        "daily/2026-09-03/finished.json": json.dumps({"status": "ok", "forecast_rc": 0, "scoring_rc": 0}).encode(),
        "daily/2026-09-02/scored.json": b"{}",
    }))
    monkeypatch.setattr(lc, "url_ok", lambda u: True)
    assert lc.audit_day("2026-09-03", "2026-09-02") == []
