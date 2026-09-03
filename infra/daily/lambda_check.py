"""latentsky-daily-check — the reaper and the deadman.

Two modes, one function, both driven by EventBridge:

  mode "reap"   every 15 minutes, all day. Terminates any latentsky-daily-* pod
                older than MAX_POD_MINUTES and alerts if it had to, or if it
                could not see RunPod at all. This is the ONLY hard bound on
                spend: RunPod has no pod TTL, the pod cannot terminate itself
                (it holds no credential), and the publisher only terminates pods
                that finish. A hung pod is caught here or not at all.

  mode "report" once, after the launch window closes. Reads the day's markers and
                alerts on every way the day can have gone wrong. A clean day
                sends nothing.

Silence is the failure mode a scheduled system hides best, so nothing here is
allowed to fail quietly:
  - every RunPod call raises on transport failure rather than returning None,
  - a termination is only reported as done once the pod is gone from the list,
  - problems found are published even if a later step throws,
  - the handler's own exception is itself an alert.

Env: DATA_BUCKET, SITE_BUCKET, TOPIC_ARN, RUNPOD_KEY_PARAM, MAX_POD_MINUTES,
     SITE_URL.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request

import boto3

DATA_BUCKET = os.environ["DATA_BUCKET"]
TOPIC_ARN = os.environ["TOPIC_ARN"]
RUNPOD_KEY_PARAM = os.environ.get("RUNPOD_KEY_PARAM", "/latentsky/runpod-api-key")
MAX_POD_MINUTES = int(os.environ.get("MAX_POD_MINUTES", "45"))
SITE_URL = os.environ.get("SITE_URL", "https://latent-sky.dev")
REGION = os.environ.get("AWS_REGION", "us-east-1")
POD_PREFIX = "latentsky-daily-"

s3 = boto3.client("s3", region_name=REGION)
sns = boto3.client("sns", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)


def exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=DATA_BUCKET, Key=key)
        return True
    except s3.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def read_json(key: str) -> dict | None:
    if not exists(key):
        return None
    return json.loads(s3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read())


def log_tail(date: str, lines: int = 14) -> str:
    key = f"daily/{date}/run.log"
    if not exists(key):
        return "(no run.log was ever shipped — the pod never started, or never reached its first upload)"
    text = s3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read().decode("utf-8", "replace")
    keep = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("+")]
    return "\n".join(keep[-lines:])


def runpod(method: str, path: str):
    """One RunPod REST call. Raises on any failure — a call that did not happen
    must never look like an empty answer."""
    key = ssm.get_parameter(Name=RUNPOD_KEY_PARAM, WithDecryption=True)["Parameter"]["Value"]
    req = urllib.request.Request(f"https://rest.runpod.io/v1{path}", method=method,
                                 headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return json.loads(body) if body else None


def list_daily_pods() -> list[dict]:
    pods = runpod("GET", "/pods") or []
    return [p for p in pods if str(p.get("name", "")).startswith(POD_PREFIX)]


def pod_age_minutes(pod: dict, now: dt.datetime) -> float | None:
    """Minutes since the pod started, or None if RunPod's timestamp is unreadable.

    Observed format: "2026-09-02 17:10:26.417 +0000 UTC".
    """
    stamp = pod.get("lastStartedAt") or pod.get("createdAt") or ""
    text = str(stamp).replace(" UTC", "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            t0 = dt.datetime.strptime(text, fmt)
            return (now - t0).total_seconds() / 60
        except ValueError:
            continue
    try:
        t0 = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=dt.timezone.utc)
        return (now - t0).total_seconds() / 60
    except ValueError:
        return None


def reap(now: dt.datetime) -> tuple[list[str], list[str]]:
    """Terminate over-age pods. Returns (killed, problems); a kill is only
    reported once the pod is actually gone."""
    problems: list[str] = []
    pods = list_daily_pods()
    targets = []
    for pod in pods:
        age = pod_age_minutes(pod, now)
        if age is None:
            # Unreadable timestamp: treat as stale. A pod we cannot age is a pod
            # we cannot bound, and leaving it running is the expensive mistake.
            problems.append(f"pod {pod.get('name')} ({pod.get('id')}) has an unparseable start time "
                            f"{pod.get('lastStartedAt') or pod.get('createdAt')!r}; terminating it on principle")
            targets.append((pod, float("inf")))
        elif age > MAX_POD_MINUTES:
            targets.append((pod, age))

    killed: list[str] = []
    for pod, age in targets:
        label = f"{pod.get('name')} ({pod.get('id')}, {'unknown' if age == float('inf') else f'{age:.0f}'} min, ${pod.get('costPerHr')}/hr)"
        try:
            runpod("DELETE", f"/pods/{pod['id']}")
        except urllib.error.HTTPError as exc:
            if exc.code not in (404, 410):
                problems.append(f"FAILED to terminate {label}: HTTP {exc.code}. It is still billing — "
                                f"terminate it by hand at runpod.io/console/pods")
                continue
        killed.append(label)

    if killed:
        # Trust the list, not the DELETE's status code.
        still = {p["id"] for p in list_daily_pods()}
        survivors = [k for k in killed if k.split("(")[1].split(",")[0] in still]
        if survivors:
            problems.append("These pods accepted a terminate and are STILL RUNNING:\n  " + "\n  ".join(survivors))
    return killed, problems


def url_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=15) as resp:
            return resp.status == 200
    except Exception:
        return False


def audit_day(date: str, prev: str) -> list[str]:
    """Every way today can have gone wrong, in the order it would have gone wrong."""
    problems: list[str] = []
    claim = read_json(f"daily/{date}/launched.json")

    if claim is None:
        problems.append(
            f"{date}: NO LAUNCH. Either the 12Z inputs never appeared on NOAA's buckets inside the "
            f"launch window, or the launcher itself failed. Check the latentsky-daily-launch logs; "
            f"if NOAA was simply late, nothing is wrong and the day is skipped."
        )
        return problems

    state = claim.get("state")
    if state == "claiming":
        problems.append(f"{date}: the day was claimed at {claim.get('claimed_at')} but the launcher never "
                        f"recorded a pod. It died between claiming and creating. No pod should exist; the "
                        f"reaper covers it if one does. Re-run with force to retry the day.")
    elif state == "launch-failed":
        problems.append(f"{date}: pod creation failed: {claim.get('error')}")
    elif state != "launched":
        problems.append(f"{date}: unexpected claim state {state!r}")

    if state == "launched":
        finished = read_json(f"daily/{date}/finished.json")
        if not exists(f"daily/{date}/site.tar.gz"):
            detail = "" if finished is None else f" The pod reported itself finished with status {finished.get('status')!r}."
            problems.append(f"{date}: pod {claim.get('pod_id')} launched at {claim.get('launched_at')} but no "
                            f"site tar ever arrived, so nothing was published.{detail}\nLog tail:\n{log_tail(date)}")
        elif not exists(f"daily/{date}/published.json"):
            problems.append(f"{date}: the site tar arrived but the publisher never recorded a publish. "
                            f"Check the latentsky-daily-publish CloudWatch logs; re-uploading the tar re-triggers it.")
        if finished is not None and finished.get("status") != "ok":
            problems.append(f"{date}: the pod finished with status {finished.get('status')!r} "
                            f"(forecast={finished.get('forecast_rc')}, scoring={finished.get('scoring_rc')}).\n"
                            f"Log tail:\n{log_tail(date)}")

        if claim.get("scores_prev"):
            if not exists(f"daily/{prev}/scored.json"):
                problems.append(f"{prev}: was due to be scored against radar in today's pod and was not.\n"
                                f"Log tail:\n{log_tail(date)}")
            else:
                # A scored day publishes a report link on the live site; a link
                # that 404s is worse than no link.
                report = f"{SITE_URL}/verification/daily-{prev}.html"
                if not url_ok(report):
                    problems.append(f"{prev}: scored, but its verification page is not reachable at {report}. "
                                    f"The event's caveat links it, so that link is currently dead.")
    return problems


def publish(subject: str, problems: list[str]) -> None:
    body = "\n\n".join(problems)
    sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:100], Message=body[:250_000])
    print(f"ALERTED: {subject}\n{body}")


def handler(event, context):
    now = dt.datetime.now(dt.timezone.utc)
    event = event if isinstance(event, dict) else {}
    mode = event.get("mode", "report")
    date = event.get("date") or now.date().isoformat()
    prev = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
    problems: list[str] = []

    try:
        # Reaping first and unconditionally: money is the thing that cannot wait.
        killed, reap_problems = reap(now)
        if killed:
            reap_problems.insert(0, "Terminated pods that were over the "
                                    f"{MAX_POD_MINUTES}-minute limit and still billing:\n  " + "\n  ".join(killed))
        problems += reap_problems
    except Exception as exc:
        note = (f"COULD NOT REACH RUNPOD to reap stale pods: {type(exc).__name__}: {exc}. "
                f"A pod may be billing right now with nothing watching it. "
                f"Check runpod.io/console/pods by hand.")
        # The reaper runs every 15 minutes; alerting from it on every failed poll
        # would send dozens of identical emails during a RunPod outage and train
        # the reader to ignore them. The daily report carries it instead.
        if mode == "report":
            problems.append(note)
        else:
            print(note)

    if mode == "report":
        try:
            problems += audit_day(date, prev)
        except Exception as exc:
            problems.append(f"The daily audit itself failed: {type(exc).__name__}: {exc}. "
                            f"The day's state is UNKNOWN — check s3://{DATA_BUCKET}/daily/{date}/ by hand.")

    if problems:
        publish(f"Latent Sky daily run: {len(problems)} problem(s) on {date}",
                [f"Latent Sky {mode} check, {now:%Y-%m-%d %H:%M}Z"] + problems)
        return {"status": "alerted", "mode": mode, "problems": problems}

    print(f"{date} ({mode}): clean")
    return {"status": "clean", "mode": mode, "date": date}
