"""latentsky-daily-launch — start today's StormCast pod once the 12Z inputs exist.

Runs every 20 minutes through the afternoon (EventBridge). Each firing:

  1. CLAIM the day (daily/<date>/launched.json). The claim is written BEFORE any
     money is spent, because the failure that matters is spending twice, not
     skipping a day. A day whose claim exists is never launched again.
  2. If the 12Z HRRR analysis or the 12Z GFS conditioning is not on NOAA's
     buckets yet, release the claim and do nothing; the next firing looks again.
  3. Otherwise presign the pod's transfers (one key each, write-only PUTs and one
     read-only GET for yesterday's stores), create the RunPod pod from the daily
     image with those URLs in env, and record the pod id in the claim.

FAIL-CLOSED ON PURPOSE. If the pod creation call fails in a way that might still
have created a pod (a socket timeout after RunPod accepted it), the claim stays
and the day is skipped. A missed day costs nothing and the deadman reports it; a
double launch costs real money on a small prepaid balance.

The claim is not a distributed lock and does not need to be: deploy-daily.sh
pins this function to reserved concurrency 1 with zero async retries, so no two
invocations of it can interleave.

No credential ever reaches the pod: the presigned URLs are the whole contract.

Env: DATA_BUCKET, IMAGE, RUNPOD_KEY_PARAM (SSM SecureString name), MEMBERS,
     CYCLE_HOUR, MAX_POD_MINUTES (recorded in the claim for the reaper).
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import urllib.error
import urllib.request

import boto3
from botocore.config import Config

DATA_BUCKET = os.environ["DATA_BUCKET"]
IMAGE = os.environ["IMAGE"]
RUNPOD_KEY_PARAM = os.environ.get("RUNPOD_KEY_PARAM", "/latentsky/runpod-api-key")
MEMBERS = os.environ.get("MEMBERS", "1")
CYCLE_HOUR = int(os.environ.get("CYCLE_HOUR", "12"))
NSTEPS = int(os.environ.get("NSTEPS", "18"))
SCORING_LOOKBACK_DAYS = int(os.environ.get("SCORING_LOOKBACK_DAYS", "7"))
REGION = os.environ.get("AWS_REGION", "us-east-1")
EXPIRY = 6 * 3600

HRRR_BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
GFS_BUCKET = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
RUNPOD_API = "https://rest.runpod.io/v1/pods"

s3 = boto3.client("s3", region_name=REGION, config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))
ssm = boto3.client("ssm", region_name=REGION)


def key_exists(key: str) -> bool:
    """True if the object exists.

    Requires unconditioned s3:ListBucket on the bucket: without it S3 answers 403
    (not 404) for a missing key and this raises. deploy-daily.sh grants it; an
    AccessDenied here means the policy regressed and must be loud, not swallowed.
    """
    try:
        s3.head_object(Bucket=DATA_BUCKET, Key=key)
        return True
    except s3.exceptions.ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def url_exists(url: str) -> bool:
    """True if the NOAA object is there; False only for a definite 403/404.

    A transport error is NOT "not ready" — it propagates, so a network problem
    reads as a failed check rather than a quietly skipped day.
    """
    req = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status == 200
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 404):
            return False
        raise


def inputs_ready(day: dt.date, hour: int) -> list[str]:
    """The NOAA objects StormCast's sources will read; returns what is still missing.

    GFS_FX is consulted at EVERY hourly step, so every lead 0..NSTEPS is checked,
    not a sample: a cycle that has published f000 but not f013 would start a run
    that dies mid-forecast having already paid for the GPU.
    """
    ymd = day.strftime("%Y%m%d")
    wanted = [
        f"{HRRR_BUCKET}/hrrr.{ymd}/conus/hrrr.t{hour:02d}z.wrfprsf00.grib2.idx",
        f"{HRRR_BUCKET}/hrrr.{ymd}/conus/hrrr.t{hour:02d}z.wrfsfcf00.grib2.idx",
        f"{HRRR_BUCKET}/hrrr.{ymd}/conus/hrrr.t{hour:02d}z.wrfnatf00.grib2.idx",
    ] + [
        f"{GFS_BUCKET}/gfs.{ymd}/{hour:02d}/atmos/gfs.t{hour:02d}z.pgrb2.0p25.f{lead:03d}.idx"
        for lead in range(0, NSTEPS + 1)
    ]
    return [u for u in wanted if not url_exists(u)]


def presign_put(key: str) -> str:
    return s3.generate_presigned_url("put_object", Params={"Bucket": DATA_BUCKET, "Key": key}, ExpiresIn=EXPIRY)


def presign_get(key: str) -> str:
    return s3.generate_presigned_url("get_object", Params={"Bucket": DATA_BUCKET, "Key": key}, ExpiresIn=EXPIRY)


def read_json(key: str) -> dict | None:
    if not key_exists(key):
        return None
    return json.loads(s3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read())


def write_json(key: str, doc: dict) -> None:
    s3.put_object(Bucket=DATA_BUCKET, Key=key, Body=json.dumps(doc, indent=1).encode(), ContentType="application/json")


def runpod_key() -> str:
    return ssm.get_parameter(Name=RUNPOD_KEY_PARAM, WithDecryption=True)["Parameter"]["Value"]


def create_pod(env: dict, name: str) -> dict:
    body = {
        "name": name,
        "imageName": IMAGE,
        "gpuTypeIds": [
            "NVIDIA A40",
            "NVIDIA RTX A6000",
            "NVIDIA RTX 6000 Ada Generation",
            "NVIDIA L40S",
            "NVIDIA L40",
            "NVIDIA RTX PRO 6000 Blackwell Server Edition",
        ],
        "gpuTypePriority": "availability",
        "gpuCount": 1,
        "cloudType": "SECURE",
        "countryCodes": ["US"],  # the ECR policy grants pull only to runpod's US roles
        "interruptible": False,
        "containerDiskInGb": 100,
        "volumeInGb": 0,
        "env": env,
    }
    req = urllib.request.Request(
        RUNPOD_API,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {runpod_key()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def plan(now: dt.datetime) -> dict:
    """Everything the handler decides, as data, so it can be tested without AWS."""
    day = now.date()
    init = dt.datetime(day.year, day.month, day.day, CYCLE_HOUR)
    prev = day - dt.timedelta(days=1)
    return {
        "date": day.isoformat(),
        "init": init.strftime("%Y-%m-%dT%H:%M:%S"),
        "event_id": f"daily-{day.isoformat()}",
        "prev_date": prev.isoformat(),
        "prev_init": dt.datetime(prev.year, prev.month, prev.day, CYCLE_HOUR).strftime("%Y-%m-%dT%H:%M:%S"),
        "prev_event_id": f"daily-{prev.isoformat()}",
    }


def pending_scoring(today: dt.date, lookback: int = None) -> tuple[str, dict] | None:
    """The newest earlier day whose forecast exists and has not been scored.

    Walks back day by day from yesterday. Returns (date, its launch marker) or
    None when there is nothing to score. Bounded by SCORING_LOOKBACK_DAYS so a
    long gap cannot make the pod fetch something arbitrarily old — and so this
    function's cost stays a handful of HEADs.
    """
    days = SCORING_LOOKBACK_DAYS if lookback is None else lookback
    for back in range(1, days + 1):
        day = (today - dt.timedelta(days=back)).isoformat()
        if not key_exists(f"daily/{day}/stores.tar.gz"):
            continue
        if key_exists(f"daily/{day}/scored.json"):
            # Scored already: everything older has had its chance too.
            return None
        return day, (read_json(f"daily/{day}/launched.json") or {})
    return None


def handler(event, context):
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    event = event if isinstance(event, dict) else {}
    # A manual invocation may name the day (tests, re-runs): {"date": "2026-09-02"}.
    if event.get("date"):
        now = dt.datetime.fromisoformat(event["date"] + "T23:00:00")
    p = plan(now)
    force = bool(event.get("force"))
    # {"check_only": true} answers "would this launch?" without creating anything.
    check_only = bool(event.get("check_only"))
    marker_key = f"daily/{p['date']}/launched.json"

    claim = read_json(marker_key)
    if claim is not None and not force:
        return {"status": "already-claimed", "claim_state": claim.get("state"), **p}

    missing = inputs_ready(dt.date.fromisoformat(p["date"]), CYCLE_HOUR)
    if missing:
        print(f"not ready: {len(missing)} of the cycle's inputs missing, first {missing[0]}")
        return {"status": "not-ready", "missing_count": len(missing), "first_missing": missing[0], **p}

    if check_only:
        return {"status": "would-launch", "note": "check_only: nothing was created", **p}

    # ── Claim the day BEFORE spending. Everything after this is best-effort; the
    #    day is never launched twice, whatever happens next. ────────────────────
    claim = {
        **p,
        "state": "claiming",
        "members": int(MEMBERS),
        "image": IMAGE,
        "max_pod_minutes": int(os.environ.get("MAX_POD_MINUTES", "45")),
        "claimed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_json(marker_key, claim)

    env = {
        "RUN_DATE": p["date"],
        "INIT": p["init"],
        "EVENT_ID": p["event_id"],
        "MEMBERS": MEMBERS,
        "PUT_LOG": presign_put(f"daily/{p['date']}/run.log"),
        "PUT_STORES": presign_put(f"daily/{p['date']}/stores.tar.gz"),
        "PUT_SITE": presign_put(f"daily/{p['date']}/site.tar.gz"),
        # Written by the pod's exit trap whatever happens, so the publisher can
        # terminate a FAILED pod as promptly as a successful one.
        "PUT_FINISHED": presign_put(f"daily/{p['date']}/finished.json"),
        "EARTH2STUDIO_CACHE": "/cache/earth2studio",
    }
    # The pod script deployed with this function overrides the one baked into the
    # image (pod_daily.sh's hot-patch hook), so a script fix is a Lambda deploy,
    # not a 25-minute image rebuild. Python changes still need the image.
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pod_daily.sh")
    if os.path.isfile(script):
        with open(script, "rb") as fh:
            env["SCRIPT_B64"] = base64.b64encode(fh.read()).decode()

    # The most recent day that has stores and has not been scored — not simply
    # yesterday. A single missed day (NOAA late, a failed pod, a day the schedule
    # was off) would otherwise orphan the run before it forever, because nothing
    # ever looks back past one day. Radar is archived, so an older day scores just
    # as well; only the lookback needs to be honest about how far it will reach.
    target = pending_scoring(dt.date.fromisoformat(p["date"]))
    if target:
        prev_date, prev_claim = target
        env.update({
            "PREV_DATE": prev_date,
            "PREV_INIT": prev_claim.get("init", f"{prev_date}T{CYCLE_HOUR:02d}:00:00"),
            "PREV_EVENT_ID": prev_claim.get("event_id", f"daily-{prev_date}"),
            "PREV_MEMBERS": str(prev_claim.get("members", MEMBERS)),
            "GET_PREV_STORES": presign_get(f"daily/{prev_date}/stores.tar.gz"),
            "PUT_PREV_SITE": presign_put(f"daily/{prev_date}/site-verified.tar.gz"),
            "PUT_PREV_REPORT": presign_put(f"daily/{prev_date}/report.html"),
            "PUT_PREV_FSS": presign_put(f"daily/{prev_date}/fss.json"),
        })

    try:
        pod = create_pod(env, f"latentsky-daily-{p['date']}")
    except Exception as exc:
        # The claim STAYS. A pod may or may not exist; the reaper kills it by name
        # within MAX_POD_MINUTES and the deadman reports the day as failed.
        claim.update({"state": "launch-failed", "error": f"{type(exc).__name__}: {exc}"[:400]})
        write_json(marker_key, claim)
        raise

    claim.update({
        "state": "launched",
        "pod_id": pod.get("id"),
        "cost_per_hr": pod.get("costPerHr"),
        "launched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "scores_prev": "GET_PREV_STORES" in env,
    })
    write_json(marker_key, claim)
    print(json.dumps(claim))
    return {"status": "launched", **claim}
