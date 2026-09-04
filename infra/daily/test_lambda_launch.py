"""The launcher's spend guarantees, tested without AWS.

Every test here corresponds to a way the 3 Sep 2026 review found this function
could create more than one $2 pod for one day.

    cd infra/daily && python -m pytest -q test_lambda_launch.py
"""

import os
import sys

import pytest

os.environ.setdefault("DATA_BUCKET", "data-bucket")
os.environ.setdefault("IMAGE", "acct.dkr.ecr.us-east-1.amazonaws.com/latentsky-forecast:test")
os.environ.setdefault("NSTEPS", "18")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lambda_launch as ll  # noqa: E402


class FakeS3:
    """Just enough S3: an in-memory keyspace with the real missing-key error."""

    class exceptions:
        class ClientError(Exception):
            def __init__(self, code):
                super().__init__(code)
                self.response = {"Error": {"Code": code}}

    def __init__(self):
        self.objects = {}
        self.writes = []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.ClientError("404")
        return {}

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body
        self.writes.append(Key)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://example.invalid/{Params['Key']}?sig=x"


@pytest.fixture
def env(monkeypatch):
    fake = FakeS3()
    created = []
    monkeypatch.setattr(ll, "s3", fake)
    monkeypatch.setattr(ll, "url_exists", lambda url: True)          # NOAA is ready
    monkeypatch.setattr(ll, "create_pod", lambda e, n: created.append(n) or {"id": "pod-1", "costPerHr": 2.09})
    return fake, created


def test_a_normal_day_launches_exactly_once(env):
    fake, created = env
    first = ll.handler({"date": "2026-09-03"}, None)
    assert first["status"] == "launched" and created == ["latentsky-daily-2026-09-03"]
    second = ll.handler({"date": "2026-09-03"}, None)
    assert second["status"] == "already-claimed"
    assert len(created) == 1, "a second firing must never create a second pod"


def test_the_day_is_claimed_before_the_pod_is_created(env, monkeypatch):
    """The bug: the marker was written AFTER create_pod, so any failure in between
    left no marker and every later firing launched again."""
    fake, created = env
    order = []
    monkeypatch.setattr(ll, "create_pod", lambda e, n: order.append("pod") or {"id": "p"})
    real_put = fake.put_object
    def spy(**kw):
        order.append("marker:" + kw["Key"])
        return real_put(**kw)
    monkeypatch.setattr(fake, "put_object", spy)
    ll.handler({"date": "2026-09-03"}, None)
    assert order[0] == "marker:daily/2026-09-03/launched.json"
    assert order.index("pod") > 0, "money was spent before the day was claimed"


def test_a_failed_pod_creation_does_not_free_the_day(env, monkeypatch):
    """Fail closed: a create that may or may not have made a pod must not let the
    next firing make another one."""
    fake, created = env
    monkeypatch.setattr(ll, "create_pod", lambda e, n: (_ for _ in ()).throw(TimeoutError("socket timeout")))
    with pytest.raises(TimeoutError):
        ll.handler({"date": "2026-09-03"}, None)
    import json
    claim = json.loads(fake.objects["daily/2026-09-03/launched.json"])
    assert claim["state"] == "launch-failed" and "TimeoutError" in claim["error"]
    monkeypatch.setattr(ll, "create_pod", lambda e, n: created.append(n) or {"id": "pod-2"})
    assert ll.handler({"date": "2026-09-03"}, None)["status"] == "already-claimed"
    assert created == [], "a second pod was created after a failed launch"


def test_not_ready_does_not_claim_the_day(env, monkeypatch):
    fake, created = env
    monkeypatch.setattr(ll, "url_exists", lambda url: False)
    out = ll.handler({"date": "2026-09-03"}, None)
    assert out["status"] == "not-ready" and created == []
    assert fake.objects == {}, "a day that could not run must stay launchable"


def test_check_only_creates_nothing(env):
    fake, created = env
    out = ll.handler({"date": "2026-09-03", "check_only": True}, None)
    assert out["status"] == "would-launch"
    assert created == [] and fake.objects == {}


def test_readiness_covers_every_hourly_conditioning_lead():
    """GFS_FX is read at every step, so a cycle missing f013 must not look ready."""
    import datetime as dt
    seen = []
    urls = ll.inputs_ready.__wrapped__ if hasattr(ll.inputs_ready, "__wrapped__") else None
    orig = ll.url_exists
    try:
        ll.url_exists = lambda u: seen.append(u) or True
        ll.inputs_ready(dt.date(2026, 9, 3), 12)
    finally:
        ll.url_exists = orig
    leads = {u.rsplit(".f", 1)[1][:3] for u in seen if "pgrb2" in u}
    assert leads == {f"{i:03d}" for i in range(19)}, f"only checked leads {sorted(leads)}"
    assert sum(1 for u in seen if "hrrr" in u) == 3


def test_a_transport_failure_is_not_read_as_not_ready(monkeypatch):
    """A network error must propagate, not silently postpone the day forever."""
    import urllib.error
    def boom(req, timeout=None):
        raise urllib.error.URLError("dns")
    monkeypatch.setattr(ll.urllib.request, "urlopen", boom)
    with pytest.raises(urllib.error.URLError):
        ll.url_exists("https://example.invalid/x")


def test_404_and_403_both_mean_absent(monkeypatch):
    import urllib.error
    for code in (403, 404):
        monkeypatch.setattr(ll.urllib.request, "urlopen",
                            lambda req, timeout=None, c=code: (_ for _ in ()).throw(
                                urllib.error.HTTPError("u", c, "no", {}, None)))
        assert ll.url_exists("https://example.invalid/x") is False


def test_plan_dates(env):
    import datetime as dt
    p = ll.plan(dt.datetime(2026, 9, 3, 16, 30))
    assert p["event_id"] == "daily-2026-09-03"
    assert p["init"] == "2026-09-03T12:00:00"
    assert p["prev_event_id"] == "daily-2026-09-02"


def test_scoring_reaches_past_a_gap_day(env):
    """A missed day must not orphan the run before it: 2 Sep has stores and no
    score, 3 Sep never ran, and the 4 Sep pod must still pick 2 Sep up."""
    fake, _ = env
    fake.objects["daily/2026-09-02/stores.tar.gz"] = b"x"
    import json
    fake.objects["daily/2026-09-02/launched.json"] = json.dumps(
        {"init": "2026-09-02T12:00:00", "event_id": "daily-2026-09-02", "members": 1}).encode()
    import datetime as dt
    target = ll.pending_scoring(dt.date(2026, 9, 4))
    assert target is not None and target[0] == "2026-09-02"
    assert target[1]["event_id"] == "daily-2026-09-02"


def test_an_already_scored_day_stops_the_walk(env):
    fake, _ = env
    fake.objects["daily/2026-09-03/stores.tar.gz"] = b"x"
    fake.objects["daily/2026-09-03/scored.json"] = b"{}"
    fake.objects["daily/2026-09-01/stores.tar.gz"] = b"x"   # older, also unscored
    import datetime as dt
    assert ll.pending_scoring(dt.date(2026, 9, 4)) is None


def test_nothing_to_score_is_not_an_error(env):
    import datetime as dt
    assert ll.pending_scoring(dt.date(2026, 9, 4)) is None


def test_the_lookback_is_bounded(env):
    fake, _ = env
    fake.objects["daily/2026-08-01/stores.tar.gz"] = b"x"
    import datetime as dt
    assert ll.pending_scoring(dt.date(2026, 9, 4)) is None, "reached back further than the bound"
