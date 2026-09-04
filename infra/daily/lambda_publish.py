"""latentsky-daily-publish — put a finished daily run on the site, and stop the pod.

Triggered by S3 object-created events on the data bucket:

  daily/<date>/site.tar.gz           today's encoded event tree
  daily/<date>/site-verified.tar.gz  yesterday's tree, re-encoded with MRMS + report link
  daily/<date>/report.html           yesterday's verification page
  daily/<date>/finished.json         the pod's exit trap: it is done, successfully or not

For a site tar: unpack it, upload the event tree under data/web/daily/<date>/ on
the SITE bucket with the same headers deploy-site.sh uses (frames and LUTs
immutable, manifest.json no-cache), rewrite catalogue.json so the newest daily
run is the default event and the last DAILY_KEEP daily runs sit first, then
invalidate exactly the entry points that changed. Nothing else on the site is
touched: the hand-curated events stay exactly as deployed.

TERMINATION IS NOT CONDITIONAL ON SUCCESS. A pod that has uploaded its site tar
(or written finished.json) has no work left, and every minute it stays up costs
about three cents. So the pod is stopped in a finally: a publish that throws
still stops the billing, and the failure is reported by the daily check.

The catalogue rules the pipeline enforces (unique ids, one default, one manifest
per event, kind/hasHero derived from the manifest) are re-applied here on the
live file, and a catalogue that would break them is not written.

Env: DATA_BUCKET, SITE_BUCKET, DISTRIBUTION_ID, RUNPOD_KEY_PARAM, DAILY_KEEP.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

import boto3

DATA_BUCKET = os.environ["DATA_BUCKET"]
SITE_BUCKET = os.environ["SITE_BUCKET"]
DISTRIBUTION_ID = os.environ["DISTRIBUTION_ID"]
RUNPOD_KEY_PARAM = os.environ.get("RUNPOD_KEY_PARAM", "/latentsky/runpod-api-key")
DAILY_KEEP = int(os.environ.get("DAILY_KEEP", "7"))
REGION = os.environ.get("AWS_REGION", "us-east-1")

IMMUTABLE = "public, max-age=31536000, immutable"
NOCACHE = "no-cache"
CONTENT_TYPES = {".webp": "image/webp", ".png": "image/png", ".json": "application/json", ".html": "text/html"}
KEY_RE = re.compile(
    r"^daily/(?P<date>\d{4}-\d{2}-\d{2})/"
    r"(?P<name>site\.tar\.gz|site-verified\.tar\.gz|report\.html|finished\.json)$"
)

s3 = boto3.client("s3", region_name=REGION)
cf = boto3.client("cloudfront", region_name=REGION)
ssm = boto3.client("ssm", region_name=REGION)


# ── pure logic (tested without AWS) ────────────────────────────────────────

def daily_entry(date: str, manifest: dict, verified: bool) -> dict:
    """The catalogue row for one daily run, capability bits read from its manifest."""
    has_hero = any(layer["kind"] == "hero-fine" for layer in manifest["layers"].values())
    init = manifest["run"].get("init") or manifest["frames"][0]
    when = dt.datetime.fromisoformat(init.replace("Z", "+00:00"))
    # No "live" and no "tomorrow". Both are baked into the catalogue at publish
    # time and neither can retract itself: an entry stays for DAILY_KEEP days
    # after it stops being the newest, and a promise of scoring "tomorrow" is
    # false the first morning a scoring pass fails. The date says which run it is;
    # the state says how far it has got, and "not yet scored" stays true however
    # long that lasts.
    title = f"Central US — daily run, {when:%H}Z {when.day} {when:%b %Y}"
    subtitle = (
        "AI forecast · StormCast, 3 km · scored against MRMS radar"
        if verified else
        "AI forecast · StormCast, 3 km · not yet scored"
    )
    return {
        "id": f"daily-{date}",
        "title": title[:60],
        "subtitle": subtitle[:120],
        "manifest": f"daily/{date}/manifest.json",
        "kind": "hero" if has_hero else "global-only",
        "region": "conus",
        "hasHero": has_hero,
        "default": False,
    }


def merge_catalogue(catalogue: dict, entry: dict, keep: int = DAILY_KEEP) -> dict:
    """Insert or refresh one daily entry; newest daily first and default; keep `keep` dailies.

    Curated (non-daily) events keep their order after the dailies and lose the
    default flag: a visitor should land on the live run. The entry handed in is
    never dropped, even if it is older than `keep` others — publishing an event
    and then omitting it from the catalogue would leave an orphan tree.
    """
    events = [e for e in catalogue.get("events", []) if e["id"] != entry["id"]]
    dailies = [e for e in events if e["id"].startswith("daily-")] + [entry]
    dailies.sort(key=lambda e: e["id"], reverse=True)
    dailies = dailies[:keep]
    if entry["id"] not in {e["id"] for e in dailies}:
        dailies = dailies[:-1] + [entry]
    curated = [e for e in events if not e["id"].startswith("daily-")]
    merged = [dict(e, default=False) for e in dailies + curated]
    if not merged:
        raise ValueError("refusing to write an empty catalogue")
    merged[0]["default"] = True

    ids = [e["id"] for e in merged]
    manifests = [e["manifest"] for e in merged]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate event ids in catalogue: {ids}")
    if len(set(manifests)) != len(manifests):
        raise ValueError(f"two events share a manifest: {manifests}")
    if sum(1 for e in merged if e["default"]) != 1:
        raise ValueError("exactly one default event required")
    for e in merged:
        if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", e["id"]) or len(e["id"]) > 64:
            raise ValueError(f"bad event id {e['id']!r}")
        for field in ("title", "subtitle", "manifest", "region", "kind"):
            if not isinstance(e.get(field), str) or not e[field]:
                raise ValueError(f"event {e['id']}: {field} missing or empty")
    return {"schemaVersion": catalogue.get("schemaVersion", 1), "events": merged}


def index_row(date: str, manifest: dict, fss: dict | None, verified: bool) -> dict:
    """One row of the verification record for this daily run.

    A row exists from the day the run is published, unscored, because the record
    is of every run rather than only the flattering ones. `scored` is driven by
    the report actually being there, so the index can never offer a link to a
    page that failed to upload.
    """
    init = manifest["run"].get("init") or manifest["frames"][0]
    when = dt.datetime.fromisoformat(init.replace("Z", "+00:00"))
    return {
        "id": f"daily-{date}",
        "title": f"Central US, {when:%H}Z {when.day} {when:%b %Y}",
        "init": init,
        "scored": bool(verified),
        "liveUrl": f"https://latent-sky.dev/?event=daily-{date}",
        "members": manifest["run"].get("members") or 1,
        "reportUrl": f"/verification/daily-{date}.html" if verified else None,
        "headline": (fss or {}).get("headline") if verified else None,
    }


def merge_index(index: dict, row: dict, keep: int = 400) -> dict:
    """Replace or insert one row, newest first. Pure, so it can be tested."""
    runs = [r for r in index.get("runs", []) if r.get("id") != row["id"]] + [row]
    runs.sort(key=lambda r: str(r.get("init") or ""), reverse=True)
    runs = runs[:keep]
    if row["id"] not in {r["id"] for r in runs}:
        runs = runs[:-1] + [row]
    if row["scored"] and not row.get("reportUrl"):
        raise ValueError("a scored row must name its report")
    ids = [r["id"] for r in runs]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate ids in the verification record: {ids}")
    return {"schemaVersion": index.get("schemaVersion", 1), "runs": runs}


def dropped_dailies(before: dict, after: dict) -> list[str]:
    """Daily ids present before the merge and gone after it (rolled off the window)."""
    b = {e["id"] for e in before.get("events", []) if e["id"].startswith("daily-")}
    a = {e["id"] for e in after.get("events", []) if e["id"].startswith("daily-")}
    return sorted(b - a)


# ── AWS side ───────────────────────────────────────────────────────────────

def upload_tree(tar_bytes: bytes, date: str) -> dict:
    """Unpack the event tree and upload it under data/web/daily/<date>/.

    manifest.json is uploaded LAST: it is the file the browser reads to find
    every other one, so it must never point at frames that are not there yet.
    """
    manifest = None
    manifest_body = None
    count = 0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/", 1)   # "<event_id>/<rel>"
            if len(parts) != 2 or ".." in member.name or member.name.startswith("/"):
                raise ValueError(f"unexpected path in site tar: {member.name}")
            rel = parts[1]
            ext = os.path.splitext(rel)[1].lower()
            if ext not in CONTENT_TYPES:
                raise ValueError(f"no content type for {rel}")
            body = tar.extractfile(member).read()
            if rel == "manifest.json":
                manifest, manifest_body = json.loads(body), body
                continue
            s3.put_object(Bucket=SITE_BUCKET, Key=f"data/web/daily/{date}/{rel}", Body=body,
                          ContentType=CONTENT_TYPES[ext], CacheControl=IMMUTABLE)
            count += 1
    if manifest is None:
        raise ValueError("site tar carried no manifest.json")
    s3.put_object(Bucket=SITE_BUCKET, Key=f"data/web/daily/{date}/manifest.json", Body=manifest_body,
                  ContentType="application/json", CacheControl=NOCACHE)
    print(f"uploaded {count} files + manifest under data/web/daily/{date}/")
    return manifest


def read_site_json(key: str) -> dict:
    return json.loads(s3.get_object(Bucket=SITE_BUCKET, Key=key)["Body"].read())


def write_site_json(key: str, doc: dict) -> None:
    s3.put_object(Bucket=SITE_BUCKET, Key=key, Body=(json.dumps(doc, indent=2) + "\n").encode(),
                  ContentType="application/json", CacheControl=NOCACHE)


def update_catalogue(entry: dict) -> tuple[dict, dict]:
    """Read-merge-write the live catalogue, then confirm our entry survived.

    S3 has no compare-and-swap here, so a concurrent writer (the other daily
    publish, or a manual deploy-site.sh) can overwrite us between the read and
    the write. Re-reading and retrying once closes the realistic window; a
    second loss is reported rather than ignored.
    """
    for attempt in (1, 2, 3):
        before = read_site_json("data/web/catalogue.json")
        after = merge_catalogue(before, entry)
        write_site_json("data/web/catalogue.json", after)
        time.sleep(1)
        confirm = read_site_json("data/web/catalogue.json")
        if any(e["id"] == entry["id"] for e in confirm.get("events", [])):
            return before, confirm
        print(f"catalogue write lost to a concurrent writer (attempt {attempt}); retrying")
    raise RuntimeError(f"catalogue kept losing {entry['id']} to a concurrent writer — check for a "
                       f"deploy-site.sh running at the same time")


def update_index(row: dict) -> list[str]:
    """Read-merge-write the verification record, then confirm the row survived.

    Same shape and same reasoning as update_catalogue: no compare-and-swap on S3,
    so a concurrent writer can lose our row between the read and the write, and a
    silent loss would mean a scored run that the index never lists.
    """
    key = "verification/index.json"
    for attempt in (1, 2, 3):
        try:
            before = read_site_json(key)
        except Exception:
            before = {"schemaVersion": 1, "runs": []}
        write_site_json(key, merge_index(before, row))
        time.sleep(1)
        confirm = read_site_json(key)
        if any(r.get("id") == row["id"] for r in confirm.get("runs", [])):
            return [r["id"] for r in confirm["runs"]]
        print(f"verification record write lost to a concurrent writer (attempt {attempt}); retrying")
    raise RuntimeError(f"the verification record kept losing {row['id']}")


def invalidate(paths: list[str]) -> str:
    resp = cf.create_invalidation(
        DistributionId=DISTRIBUTION_ID,
        InvalidationBatch={"Paths": {"Quantity": len(paths), "Items": paths},
                           "CallerReference": f"daily-{paths[0]}-{time.time()}"},
    )
    return resp["Invalidation"]["Id"]


def terminate_pod(date: str) -> str:
    """Stop the day's pod. Called whatever the publish outcome — a pod with
    nothing left to do must not keep billing because our upload failed."""
    try:
        claim = json.loads(s3.get_object(Bucket=DATA_BUCKET, Key=f"daily/{date}/launched.json")["Body"].read())
    except Exception as exc:
        return f"could not read the launch marker ({type(exc).__name__}); the reaper will catch the pod by name"
    pod_id = claim.get("pod_id")
    if not pod_id:
        return "no pod id in the launch marker; the reaper will catch it by name"
    time.sleep(20)  # let the pod's exit trap ship its final log
    key = ssm.get_parameter(Name=RUNPOD_KEY_PARAM, WithDecryption=True)["Parameter"]["Value"]
    req = urllib.request.Request(f"https://rest.runpod.io/v1/pods/{pod_id}", method="DELETE",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return f"terminated pod {pod_id} (HTTP {resp.status})"
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            return f"pod {pod_id} was already gone"
        return f"FAILED to terminate pod {pod_id}: HTTP {exc.code} — the reaper will retry"
    except Exception as exc:
        return f"FAILED to terminate pod {pod_id}: {type(exc).__name__} — the reaper will retry"


def publish_site(date: str, key: str, verified: bool) -> dict:
    tar_bytes = s3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read()
    manifest = upload_tree(tar_bytes, date)
    before, after = update_catalogue(daily_entry(date, manifest, verified))
    # The verification record lists every run from the day it is published, so a
    # reader sees the whole series rather than only the days that scored well.
    # A scored row reads its headline from the results the pod uploaded, which
    # pod_daily.sh always PUTs before the site tar, so the figure exists by then.
    fss = None
    if verified:
        try:
            fss = json.loads(s3.get_object(Bucket=DATA_BUCKET, Key=f"daily/{date}/fss.json")["Body"].read())
        except Exception as exc:
            print(f"{date}: no results file for the index row ({type(exc).__name__})")
    listed = update_index(index_row(date, manifest, fss, verified))
    print(f"verification record now lists {len(listed)} run(s)")
    paths = ["/data/web/catalogue.json", f"/data/web/daily/{date}/manifest.json", "/verification/index.json"]
    result = {"date": date, "verified": verified, "invalidation": invalidate(paths),
              "events": [e["id"] for e in after["events"]], "rolled_off": dropped_dailies(before, after)}
    marker = f"daily/{date}/{'scored.json' if verified else 'published.json'}"
    s3.put_object(Bucket=DATA_BUCKET, Key=marker,
                  Body=json.dumps({**result, "at": dt.datetime.now(dt.timezone.utc).isoformat()}).encode(),
                  ContentType="application/json")
    return result


def publish_report(date: str, key: str) -> dict:
    body = s3.get_object(Bucket=DATA_BUCKET, Key=key)["Body"].read()
    site_key = f"verification/daily-{date}.html"
    s3.put_object(Bucket=SITE_BUCKET, Key=site_key, Body=body, ContentType="text/html", CacheControl=NOCACHE)
    return {"date": date, "report": site_key, "invalidation": invalidate([f"/{site_key}"])}


def handler(event, context):
    results = []
    for record in event.get("Records", []):
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        m = KEY_RE.match(key)
        if not m:
            print(f"ignoring {key}")
            continue
        date, name = m["date"], m["name"]

        if name == "finished.json":
            # The pod's exit trap fired. Whether it succeeded or not, it is done.
            print(f"{date}: pod reported finished — {terminate_pod(date)}")
            results.append({"date": date, "action": "terminated-on-finish"})
            continue

        if name == "report.html":
            results.append(publish_report(date, key))
            continue

        # site.tar.gz / site-verified.tar.gz — the publish may fail; the pod is
        # stopped either way, because it has nothing left to do.
        try:
            results.append(publish_site(date, key, verified=(name == "site-verified.tar.gz")))
        finally:
            if name == "site.tar.gz":
                print(f"{date}: {terminate_pod(date)}")
    print(json.dumps(results, default=str))
    return results
