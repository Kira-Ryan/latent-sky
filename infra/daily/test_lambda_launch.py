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


def test_the_claim_names_the_day_the_pod_will_score(env):
    """The claim is what the deadman audits. On 4 Sep 2026 it said 'prev_date:
    2026-09-03' (yesterday, from plan()) while the pod was sent to score 2 Sep,
    and the audit raised a false alarm about 3 Sep."""
    import json
    fake, _ = env
    fake.objects["daily/2026-09-02/stores.tar.gz"] = b"x"
    fake.objects["daily/2026-09-02/launched.json"] = json.dumps(
        {"init": "2026-09-02T12:00:00", "event_id": "daily-2026-09-02", "members": 1}).encode()
    ll.handler({"date": "2026-09-04"}, None)
    claim = json.loads(fake.objects["daily/2026-09-04/launched.json"])
    assert claim["scores_prev"] is True
    assert claim["prev_date"] == "2026-09-02" and claim["prev_event_id"] == "daily-2026-09-02"


def test_the_claim_says_nothing_is_scored_when_nothing_is(env):
    import json
    fake, _ = env
    ll.handler({"date": "2026-09-04"}, None)
    claim = json.loads(fake.objects["daily/2026-09-04/launched.json"])
    assert claim["scores_prev"] is False and claim["prev_date"] is None


def test_two_firings_in_the_same_seconds_create_one_pod(env, monkeypatch):
    """The claim is a conditional create. Both firings read 'no claim'; only the
    first write lands, the second gets 412 and stands down. Before this the
    second firing overwrote the marker and launched a second pod."""
    fake, created = env
    real_put = fake.put_object

    def conditional(**kw):
        if kw.get("IfNoneMatch") == "*" and kw["Key"] in fake.objects:
            raise fake.exceptions.ClientError("PreconditionFailed")
        kw.pop("IfNoneMatch", None)
        return real_put(**kw)

    monkeypatch.setattr(fake, "put_object", conditional)
    monkeypatch.setattr(ll, "read_json", lambda key: None)   # both firings see no claim
    first = ll.handler({"date": "2026-09-03"}, None)
    second = ll.handler({"date": "2026-09-03"}, None)
    assert first["status"] == "launched"
    assert second["status"] == "already-claimed"
    assert created == ["latentsky-daily-2026-09-03"], "the race produced a second pod"


def test_an_old_runtime_falls_back_to_the_unconditional_claim(env, monkeypatch):
    """A botocore without conditional writes must not turn every day into a
    crash: it falls back to the read-then-write claim (the previous behaviour)
    and says so."""
    fake, created = env
    real_put = fake.put_object

    def old_botocore(**kw):
        if "IfNoneMatch" in kw:
            raise ll.ParamValidationError(report="Unknown parameter in input: IfNoneMatch")
        return real_put(**kw)

    monkeypatch.setattr(fake, "put_object", old_botocore)
    assert ll.handler({"date": "2026-09-03"}, None)["status"] == "launched"
    assert ll.handler({"date": "2026-09-03"}, None)["status"] == "already-claimed"
    assert created == ["latentsky-daily-2026-09-03"]


# ── the 6-8 Sep 2026 outage: Cloudflare 1010 on Python's default agent ────────
#
# These exercise the REAL create_pod and url_exists, so they must NOT take the
# `env` fixture, which stubs both out.

@pytest.fixture
def key(monkeypatch):
    monkeypatch.setattr(ll, "ssm", type("S", (), {"get_parameter": lambda self, **kw: {"Parameter": {"Value": "k"}}})())
    monkeypatch.setattr(ll.time, "sleep", lambda s: None)


class _Resp:
    status = 200

    def __init__(self, body=b'{"id": "pod-1", "costPerHr": 2.09}'):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(code=403, body=b"error code: 1010"):
    import io
    return ll.urllib.error.HTTPError("https://rest.runpod.io/v1/pods", code, "Forbidden", {}, io.BytesIO(body))


def test_every_runpod_request_names_itself(key, monkeypatch):
    """The regression test for two lost days. RunPod sits behind Cloudflare,
    which refuses "Python-urllib/3.x" with a 1010; an unnamed request gets a
    bare 403 and the day dies. NOAA's HEAD checks are named for the same reason:
    there, a 403 is read as "absent" and would skip the day silently for ever."""
    seen = []
    monkeypatch.setattr(ll.urllib.request, "urlopen", lambda req, timeout=None: seen.append(dict(req.headers)) or _Resp())
    ll.create_pod({"RUN_DATE": "2026-09-08"}, "latentsky-daily-2026-09-08")
    ll.url_exists("https://noaa-hrrr-bdp-pds.s3.amazonaws.com/x.idx")
    assert len(seen) == 2, "both calls must be made"
    for headers in seen:
        ua = headers.get("User-agent") or headers.get("User-Agent") or ""
        assert ua and "urllib" not in ua.lower(), f"unnamed client: {headers}"


def test_a_rejected_create_is_retried_because_no_pod_was_made(key, monkeypatch):
    """A response came back, so the request was refused and nothing was created:
    retrying cannot spend twice. A single edge 403 must not cost the day."""
    calls = []

    def flaky(req, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            raise _http_error()
        return _Resp()

    monkeypatch.setattr(ll.urllib.request, "urlopen", flaky)
    assert ll.create_pod({}, "latentsky-daily-2026-09-08")["id"] == "pod-1"
    assert len(calls) == 3


def test_a_create_timeout_is_never_retried(key, monkeypatch):
    """A timeout may have created a pod we never heard about. Fail closed: one
    attempt, then propagate, and the day stays claimed."""
    calls = []

    def times_out(req, timeout=None):
        calls.append(1)
        raise TimeoutError("socket timeout")

    monkeypatch.setattr(ll.urllib.request, "urlopen", times_out)
    with pytest.raises(TimeoutError):
        ll.create_pod({}, "latentsky-daily-2026-09-08")
    assert len(calls) == 1, "a possibly-created pod must never be retried"


def test_what_the_server_said_reaches_the_alert(key, monkeypatch):
    """"403 Forbidden" alone cost two days; "error code: 1010" is the answer."""
    monkeypatch.setattr(ll.urllib.request, "urlopen", lambda req, timeout=None: (_ for _ in ()).throw(_http_error()))
    with pytest.raises(RuntimeError, match="1010"):
        ll.create_pod({}, "latentsky-daily-2026-09-08")


def test_a_refused_create_still_leaves_the_day_claimed(env, monkeypatch):
    fake, created = env
    monkeypatch.setattr(ll, "create_pod", lambda e, n: (_ for _ in ()).throw(
        RuntimeError("RunPod refused the create (HTTP 403 Forbidden — error code: 1010)")))
    with pytest.raises(RuntimeError):
        ll.handler({"date": "2026-09-08"}, None)
    import json
    claim = json.loads(fake.objects["daily/2026-09-08/launched.json"])
    assert claim["state"] == "launch-failed" and "1010" in claim["error"]
