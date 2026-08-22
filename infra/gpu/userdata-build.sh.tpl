#!/bin/bash
# Remote image build — TEMPLATE. build-image-remote.sh substitutes __VARS__.
# Builds the forecast image INSIDE AWS (fat pipe to nvcr.io + ECR) so the ~46 GB
# image never crosses a home connection. Same safety DNA as the GPU userdata:
# deadman first, poweroff never halt, logs uploaded win or lose.

# ── DEADMAN: nothing may precede this line. ─────────────────────────────────
( sleep __DEADLINE_SECONDS__; poweroff ) &

set -x
exec > /var/log/latentsky-build.log 2>&1

REGION=__REGION__
BUCKET=__BUCKET__
ECR_URI=__ECR_URI__
TAG=__TAG__

timeout __JOB_TIMEOUT__ bash -c '
  set -e
  apt-get update -y
  apt-get install -y docker.io awscli
  systemctl start docker

  mkdir -p /build && cd /build
  aws s3 cp "s3://'"$BUCKET"'/build/context.tar.gz" . --region '"$REGION"'
  tar xzf context.tar.gz

  docker build -t "'"$ECR_URI"':'"$TAG"'" .

  aws ecr get-login-password --region '"$REGION"' \
    | docker login --username AWS --password-stdin "'"${ECR_URI%%/*}"'"
  docker push "'"$ECR_URI"':'"$TAG"'"
  echo "PUSH COMPLETE"
'
JOB_RC=$?

aws s3 cp /var/log/latentsky-build.log "s3://$BUCKET/build/build.log" --region "$REGION"
echo "build job exit code: $JOB_RC"
poweroff
