# The daily run

Every day, StormCast forecasts the central US from the 12Z HRRR analysis, the
result goes on the site as the default event, and the day after it is scored
against MRMS radar and re-published with the observed layer and its
verification page. Nothing runs between days. The site stays static.

## What runs where

| Piece | Where | When | Does |
|---|---|---|---|
| `latentsky-daily-launch` | Lambda | every 20 min, 15:05 to 19:45Z | waits for the 12Z inputs on NOAA's buckets, claims the day, then starts one RunPod pod with presigned URLs |
| `pod_daily.sh` | the pod (`latentsky-forecast:0.17.0-daily`) | ~10 min | scores yesterday, forecasts today, encodes, uploads two tars and a page |
| `latentsky-daily-publish` | Lambda, S3 trigger | on each upload | unpacks onto the site bucket, rewrites the catalogue, invalidates, terminates the pod |
| `latentsky-daily-check` (`mode=reap`) | Lambda | every 15 min, all day | terminates any daily pod past `MAX_POD_MINUTES` |
| `latentsky-daily-check` (`mode=report`) | Lambda | 20:35Z | the deadman: emails on any missing step |

The pod never holds a credential. The launcher presigns exactly the keys the
pod may write (`daily/<date>/run.log`, `stores.tar.gz`, `site.tar.gz`,
`finished.json`, and yesterday's `report.html`, `fss.json`,
`site-verified.tar.gz`) plus one GET for yesterday's stores. The RunPod API key
lives in SSM as a SecureString and is read only by the Lambdas.

## What bounds the spend

A GPU pod costs about $2 an hour and RunPod has no pod TTL, so three
independent things have to hold, and each is there because a review on
3 September 2026 found the case where the others do not.

1. **One pod per day.** The launcher writes `daily/<date>/launched.json` as a
   claim *before* it creates anything. If pod creation then fails in a way that
   might still have made a pod, the claim stays and the day is skipped. The
   function also runs at reserved concurrency 1 with async retries disabled, so
   no two firings can interleave or replay.
2. **A finished pod is stopped immediately.** The pod's exit trap always writes
   `finished.json`, success or failure, and the publisher terminates on that as
   well as on the site tar. Termination is never conditional on the publish
   succeeding.
3. **A hung pod is stopped anyway.** The reaper runs every 15 minutes all day
   and kills any `latentsky-daily-*` pod older than `MAX_POD_MINUTES` (45 for a
   single run, 75 for an ensemble). It confirms the pod is actually gone rather
   than trusting the terminate call, and it treats an unreadable start time as
   stale.

Worst realistic case is therefore one pod living `MAX_POD_MINUTES`, about
$1.50, and the daily report says so by email.

## Why RunPod and not EC2

The account's quota for G-family instances is 0 (request L-DB2E81BA of 22
August 2026 closed without an increase), so the GPU is rented per run. The
image is pulled from the account's ECR by RunPod's US deployment roles; pods
are pinned to US datacentres for that reason.

## Timeline of one day (UTC)

- 12:00 cycle time. HRRR analysis lands about 12:50; GFS 0.25 degree f000 to
  f018 land about 15:40 to 15:50 (measured over seven cycles).
- 15:45 to 16:05 the launcher sees every input and starts the pod.
- ~16:15 site tar arrives; the publisher puts `daily-<date>` on the site as the
  default event, subtitled "verification against radar tomorrow".
- next day ~16:15 the next pod has scored it against MRMS; the publisher
  replaces the tree with the verified one (observed radar layer, probability
  pair if members were run, report link) and uploads
  `/verification/daily-<date>.html`.
- 20:35 the check runs. A clean day sends nothing.

The catalogue keeps the last `DAILY_KEEP` (7) daily runs, newest first and
default, then the curated events. Older daily trees stay in the site bucket,
unreferenced.

## Deploying and changing it

```
cd infra/daily
source ../gpu/latentsky.env
RUNPOD_API_KEY=... ./deploy-daily.sh          # first time: writes the SSM secret
./deploy-daily.sh --dry-run                    # read-only walk-through
```

- A change to `pipeline/pod_daily.sh` is a `deploy-daily.sh` run: the launcher
  ships the current script to every pod it starts (the baked copy is the
  fallback).
- A change to any Python under `pipeline/` (forecast, encoder, verification,
  report template) is an image rebuild:
  `DOCKERFILE=Dockerfile.daily BASE_TAG=0.17.0-stormcast TAG=0.17.0-daily ../gpu/build-image-remote.sh`
  (about 25 minutes on a disposable c6i.2xlarge), then `deploy-daily.sh` if
  the tag changed.
- `MEMBERS=8 ./deploy-daily.sh` turns on the ensemble (about 17 extra pod
  minutes per day; the probability layer and the probabilistic scores follow
  automatically).

## Testing without waiting for tomorrow

Ask whether today would launch, without creating anything:

```
aws lambda invoke --function-name latentsky-daily-launch --region us-east-1 \
  --cli-binary-format raw-in-base64-out --payload '{"check_only":true}' /dev/stdout
```

Launch a real pod for a past day. This one costs money and puts that day on the
live site:

```
aws lambda invoke --function-name latentsky-daily-launch --region us-east-1 \
  --cli-binary-format raw-in-base64-out --payload '{"date":"2026-09-02"}' /dev/stdout
```

A past date whose inputs exist launches a real pod for that day (a few tens of
cents). The day after it in the calendar will score it when that day's pod
runs, or invoke the launcher with `{"date": "<next day>", "force": true}`
once that day's inputs exist. Watch the pod through its log:
`aws s3 cp s3://latentsky-<account>/daily/<date>/run.log -`.

## When the alert email arrives

The message names the step that did not happen and pastes the tail of the pod
log. In order of likelihood: the inputs never arrived (NOAA outage, or a cycle
delayed past 19:45Z; nothing to do, the day is skipped), the pod failed (read
the log, fix, invoke the launcher with `force`), the publisher failed (check
its CloudWatch log; the tar is still in the data bucket and re-uploading it
re-triggers the publisher). Pods the check killed were still billing; the
email says which.

## Cost

One pod of roughly 10 minutes on a 48 GB card, plus the image pull: under
$0.50 a day at the prices seen so far, under $1 with the ensemble. Lambda,
EventBridge, SNS and the S3 traffic are cents a month.
