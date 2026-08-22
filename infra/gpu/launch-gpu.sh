#!/bin/bash
# Launch the Latent Sky forecast run on a g6e.2xlarge (L40S 48 GB), us-east-1.
#
#   ./launch-gpu.sh --config event_doksuri_2023.yaml [--dry-run-instance]
#
# Prerequisites (once): the L-DB2E81BA quota approved; a VPC public subnet with an
# S3 GATEWAY endpoint (never a NAT Gateway — §9.4); the image pushed to ECR; the
# models prefetched to s3://$BUCKET/models/. See RUNBOOK.md.
#
# Safety properties this script guarantees:
#   --instance-initiated-shutdown-behavior terminate   poweroff => terminated
#   DeleteOnTermination=true on the root volume        no orphan EBS billing
#   deadman `( sleep N; poweroff ) &` as userdata line one
#   IMDSv2 required, 8 vCPU G-family only (P quota stays 0 — the $55/hr tier is
#   physically unlaunchable)

set -euo pipefail
source "$(dirname "$0")/account_guard.sh"

cd "$(dirname "$0")"

REGION=${REGION:-us-east-1}
INSTANCE_TYPE=${INSTANCE_TYPE:-g6e.2xlarge}      # fallback: g7e.2xlarge (96 GB, same quota)
BUCKET=${BUCKET:?set BUCKET=<your-s3-bucket>}
IMAGE=${IMAGE:?set IMAGE=<account>.dkr.ecr.$REGION.amazonaws.com/latentsky-forecast:0.17.0-models}
KEY_NAME=${KEY_NAME:?set KEY_NAME=<ec2 keypair name>}
SUBNET_ID=${SUBNET_ID:?set SUBNET_ID=<public subnet with S3 gateway endpoint>}
SG_ID=${SG_ID:?set SG_ID=<security group: egress-only is enough>}
PROFILE_ARN=${PROFILE_ARN:?set PROFILE_ARN=<instance profile with S3+ECR read/write>}
DEADLINE_SECONDS=${DEADLINE_SECONDS:-14400}       # 4 h hard ceiling ≈ $9 worst case
JOB_TIMEOUT=${JOB_TIMEOUT:-3h}

CONFIG=""
while [[ $# -gt 0 ]]; do case "$1" in
  --config) CONFIG="$2"; shift 2 ;;
  *) echo "unknown arg $1"; exit 2 ;;
esac; done
[[ -n "$CONFIG" ]] || { echo "--config <event_*.yaml> is required"; exit 2; }

# Latest Ubuntu 24.04 AMI via SSM public parameter — never a hardcoded AMI id.
AMI=$(aws ssm get-parameter --region "$REGION" \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)

sed -e "s|__DEADLINE_SECONDS__|$DEADLINE_SECONDS|" \
    -e "s|__JOB_TIMEOUT__|$JOB_TIMEOUT|" \
    -e "s|__REGION__|$REGION|" \
    -e "s|__BUCKET__|$BUCKET|" \
    -e "s|__CONFIG__|$CONFIG|" \
    -e "s|__IMAGE__|$IMAGE|" \
    userdata.sh.tpl > /tmp/latentsky-userdata.sh

echo "Launching $INSTANCE_TYPE in $REGION — deadman ${DEADLINE_SECONDS}s, config $CONFIG"
aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --subnet-id "$SUBNET_ID" \
  --security-group-ids "$SG_ID" \
  --iam-instance-profile "Arn=$PROFILE_ARN" \
  --instance-initiated-shutdown-behavior terminate \
  --metadata-options "HttpTokens=required,HttpPutResponseHopLimit=2" \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":300,"VolumeType":"gp3","DeleteOnTermination":true}}]' \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=latentsky-forecast},{Key=project,Value=latent-sky}]" \
  --user-data file:///tmp/latentsky-userdata.sh \
  --query 'Instances[0].InstanceId' --output text

echo
echo "REMEMBER: set a phone alarm for $((DEADLINE_SECONDS / 3600)) hours from now."
echo "Watch:   aws ec2 describe-instances --region $REGION --filters Name=tag:Name,Values=latentsky-forecast --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table"
