#!/bin/bash
# Remote image build — TEMPLATE. build-image-remote.sh substitutes __VARS__.
# Builds the forecast image INSIDE AWS (fat pipe to nvcr.io + ECR) so the ~46 GB
# image never crosses a home connection. Deadman first, poweroff never halt,
# breadcrumbs and logs uploaded win or lose.
#
# Lesson from the first attempt (22 Aug 2026): Ubuntu noble has no `awscli` apt
# package, so a combined `apt-get install docker.io awscli` died instantly and
# set -e silenced everything — no log ever reached S3. The toolchain phase is now
# per-step verified, aws-cli comes from snap, and an early "started" marker proves
# credentials + CLI before the long work begins.

# ── DEADMAN: nothing may precede this line. ─────────────────────────────────
( sleep __DEADLINE_SECONDS__; poweroff ) &

set -x
exec > /var/log/latentsky-build.log 2>&1

REGION=__REGION__
BUCKET=__BUCKET__
ECR_URI=__ECR_URI__
TAG=__TAG__
BAKE_MODELS=__BAKE_MODELS__
DOCKERFILE=__DOCKERFILE__
BASE_TAG=__BASE_TAG__

upload_log() {
  command -v aws >/dev/null 2>&1 && \
    aws s3 cp /var/log/latentsky-build.log "s3://$BUCKET/build/build.log" --region "$REGION"
}
trap 'upload_log; poweroff' EXIT

# ── Toolchain: each step verified, nothing dies silently ────────────────────
apt-get update -y
apt-get install -y docker.io
command -v aws >/dev/null 2>&1 || snap install aws-cli --classic
command -v docker >/dev/null 2>&1 || { echo "FATAL: docker install failed"; exit 1; }
command -v aws    >/dev/null 2>&1 || { echo "FATAL: aws cli install failed"; exit 1; }
systemctl start docker

# Breadcrumb: proves role credentials + CLI before the long work begins.
echo "boot ok $(date -u +%FT%TZ)" > /tmp/started.txt
aws s3 cp /tmp/started.txt "s3://$BUCKET/build/started.txt" --region "$REGION" \
  || { echo "FATAL: S3 write failed — instance role broken"; exit 1; }

# ── The build ───────────────────────────────────────────────────────────────
timeout __JOB_TIMEOUT__ bash -c '
  set -e
  mkdir -p /build && cd /build
  aws s3 cp "s3://'"$BUCKET"'/build/context.tar.gz" . --region '"$REGION"'
  tar xzf context.tar.gz
  # Login BEFORE build: Dockerfile.bake pulls its FROM image from our private ECR.
  aws ecr get-login-password --region '"$REGION"' \
    | docker login --username AWS --password-stdin "'"${ECR_URI%%/*}"'"
  docker build -f '"$DOCKERFILE"' \
    --build-arg BAKE_MODELS='"$BAKE_MODELS"' \
    --build-arg BASE_IMAGE="'"$ECR_URI"':'"$BASE_TAG"'" \
    -t "'"$ECR_URI"':'"$TAG"'" .
  docker push "'"$ECR_URI"':'"$TAG"'"
  echo "PUSH COMPLETE"
'
echo "build job exit code: $?"
# trap handles log upload + poweroff
