#!/bin/bash
# Build the forecast image INSIDE AWS and push to ECR — Option C from the runbook.
# The ~46 GB image never crosses a home connection: a c6i.2xlarge pulls the nvcr
# base over AWS's pipe, builds from a tiny S3-staged context, pushes to ECR from
# inside the datacentre, and terminates itself. Runs under the standard-instance
# quota — no GPU quota needed.
#
#   source ./latentsky.env && ./build-image-remote.sh
#
# Safety: deadman line one, terminate-on-shutdown, DeleteOnTermination, account guard.

set -euo pipefail
source "$(dirname "$0")/account_guard.sh"
cd "$(dirname "$0")"

REGION=${REGION:-us-east-1}
BUCKET=${BUCKET:?source latentsky.env first}
SUBNET_ID=${SUBNET_ID:?source latentsky.env first}
SG_ID=${SG_ID:?source latentsky.env first}
INSTANCE_TYPE=${BUILD_INSTANCE_TYPE:-c6i.2xlarge}     # 8 vCPU — inside the 16-vCPU standard quota
ECR_URI="${LATENTSKY_AWS_ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/latentsky-forecast"
TAG=${TAG:-0.17.0}
DEADLINE_SECONDS=${DEADLINE_SECONDS:-10800}           # 3 h hard ceiling ≈ $1 worst case
JOB_TIMEOUT=${JOB_TIMEOUT:-150m}
PROFILE_NAME="latentsky-build"

# ── One-time IAM: role + instance profile (idempotent) ─────────────────────────
if ! aws iam get-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null 2>&1; then
  echo "creating IAM role + instance profile $PROFILE_NAME…"
  aws iam create-role --role-name "$PROFILE_NAME" --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
  aws iam put-role-policy --role-name "$PROFILE_NAME" --policy-name latentsky-build-access \
    --policy-document '{
    "Version":"2012-10-17",
    "Statement":[
      {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],
       "Resource":"arn:aws:s3:::'"$BUCKET"'/build/*"},
      {"Effect":"Allow","Action":"ecr:GetAuthorizationToken","Resource":"*"},
      {"Effect":"Allow","Action":["ecr:BatchCheckLayerAvailability","ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart","ecr:CompleteLayerUpload","ecr:PutImage","ecr:BatchGetImage",
        "ecr:GetDownloadUrlForLayer"],
       "Resource":"arn:aws:ecr:'"$REGION"':'"$LATENTSKY_AWS_ACCOUNT"':repository/latentsky-forecast"}]}' >/dev/null
  aws iam create-instance-profile --instance-profile-name "$PROFILE_NAME" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$PROFILE_NAME" --role-name "$PROFILE_NAME" >/dev/null
  echo "waiting 12 s for IAM propagation…"; sleep 12
fi

# ── Stage the build context (Dockerfile + configs + the two copied files) ──────
echo "staging build context to s3://$BUCKET/build/context.tar.gz…"
tar czf /tmp/latentsky-context.tar.gz -C ../../pipeline \
  Dockerfile configs src/latentsky/forecast.py tools/bake_models.py
aws s3 cp /tmp/latentsky-context.tar.gz "s3://$BUCKET/build/context.tar.gz" --region "$REGION" --only-show-errors
echo "context: $(wc -c < /tmp/latentsky-context.tar.gz) bytes"

# ── Userdata from template ─────────────────────────────────────────────────────
# Native-visible temp path: the AWS CLI is a Windows exe under Git Bash, so
# /tmp/... is invisible to it (tested 22 Aug 2026). cygpath bridges the two worlds.
NATIVE_TMP="$( (command -v cygpath >/dev/null 2>&1 && cygpath -m "${TEMP:-/tmp}") || echo /tmp )"
sed -e "s|__DEADLINE_SECONDS__|$DEADLINE_SECONDS|" \
    -e "s|__JOB_TIMEOUT__|$JOB_TIMEOUT|" \
    -e "s|__REGION__|$REGION|" \
    -e "s|__BUCKET__|$BUCKET|" \
    -e "s|__ECR_URI__|$ECR_URI|" \
    -e "s|__TAG__|$TAG|" \
    userdata-build.sh.tpl > "$NATIVE_TMP/latentsky-userdata-build.sh"

# Newest Canonical Ubuntu 24.04 AMI by creation date. (The SSM public-parameter
# path for noble returned ParameterNotFound when tested 22 Aug 2026 — describe-images
# against Canonical's owner id is the lookup that actually works.)
AMI=$(aws ec2 describe-images --region "$REGION" --owners 099720109477   --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"             "Name=state,Values=available"   --query "sort_by(Images,&CreationDate)[-1].ImageId" --output text)

echo "launching $INSTANCE_TYPE (deadman ${DEADLINE_SECONDS}s) to build $ECR_URI:$TAG…"
aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" \
  --instance-type "$INSTANCE_TYPE" \
  --subnet-id "$SUBNET_ID" \
  --security-group-ids "$SG_ID" \
  --iam-instance-profile "Name=$PROFILE_NAME" \
  --instance-initiated-shutdown-behavior terminate \
  --metadata-options "HttpTokens=required,HttpPutResponseHopLimit=2" \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":150,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=latentsky-image-build},{Key=project,Value=latent-sky}]" \
  --user-data "file://$NATIVE_TMP/latentsky-userdata-build.sh" \
  --query 'Instances[0].InstanceId' --output text

echo
echo "Watch:  aws ec2 describe-instances --region $REGION --filters Name=tag:Name,Values=latentsky-image-build --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table"
echo "Log:    aws s3 cp s3://$BUCKET/build/build.log - (after it finishes)"
echo "Done when: aws ecr describe-images --repository-name latentsky-forecast --region $REGION shows tag $TAG"
