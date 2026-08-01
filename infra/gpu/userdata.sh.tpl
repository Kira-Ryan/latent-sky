#!/bin/bash
# Latent Sky GPU userdata — TEMPLATE. launch-gpu.sh substitutes __VARS__ and passes it
# to run-instances. Ordering here is the safety design (Architecture.md §9.4):
#
#   LINE ONE is a detached hard deadline that fires no matter what else fails.
#   `poweroff`, NEVER `halt` — halt idles the CPU and keeps billing while looking done.
#   The instance is launched with --instance-initiated-shutdown-behavior terminate and
#   DeleteOnTermination=true, so poweroff == terminated == billing stops.

# ── DEADMAN: nothing may precede this line. ──────────────────────────────────────────
( sleep __DEADLINE_SECONDS__; poweroff ) &

set -x
exec > /var/log/latentsky-run.log 2>&1

REGION=__REGION__
BUCKET=__BUCKET__
CONFIG=__CONFIG__
IMAGE=__IMAGE__
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)

# Everything below runs under a timeout half the deadman, belt and braces.
timeout __JOB_TIMEOUT__ bash -c '
  set -e
  mkdir -p /out /cache

  # Model cache: prefer the S3 seed (free via the Gateway endpoint) over NGC.
  aws s3 sync "s3://'"$BUCKET"'/models/earth2studio/" /cache/earth2studio/ --region '"$REGION"' --only-show-errors || true

  docker pull '"$IMAGE"'
  docker run --rm --gpus all \
    -v /out:/out -v /cache:/cache \
    -e EARTH2STUDIO_CACHE=/cache/earth2studio \
    '"$IMAGE"' --config /opt/latentsky/configs/'"$CONFIG"'
'
JOB_RC=$?

# Upload results and the log REGARDLESS of job outcome — a failed run's log is the
# product of that run.
aws s3 sync /out "s3://$BUCKET/runs/$RUN_ID/" --region "$REGION" --only-show-errors
aws s3 cp /var/log/latentsky-run.log "s3://$BUCKET/runs/$RUN_ID/run.log" --region "$REGION"
echo "job exit code: $JOB_RC"

poweroff
