#!/bin/bash
# PROBE 5 — rehearse the deadman switch on a t3.micro for about one cent.
#
#   ./rehearse-deadman.sh          # launches, waits, verifies, reports
#
# Validates the only cost control fast enough to matter (§9.4): that the exact
# userdata shape reaches state `terminated` (NOT `stopped`) and that the root EBS
# volume is deleted with it. Uses `sleep 120` in place of the 4-hour deadline.

set -euo pipefail
source "$(dirname "$0")/account_guard.sh"


REGION=${REGION:-us-east-1}
KEY_NAME=${KEY_NAME:?set KEY_NAME=<ec2 keypair name>}
SUBNET_ID=${SUBNET_ID:?set SUBNET_ID=<any public subnet>}
SG_ID=${SG_ID:?set SG_ID=<any security group>}

# Newest Canonical Ubuntu 24.04 AMI by creation date. (The SSM public-parameter
# path for noble returned ParameterNotFound when tested 22 Aug 2026 — describe-images
# against Canonical's owner id is the lookup that actually works.)
AMI=$(aws ec2 describe-images --region "$REGION" --owners 099720109477   --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"             "Name=state,Values=available"   --query "sort_by(Images,&CreationDate)[-1].ImageId" --output text)

# Native-visible temp path: the AWS CLI is a Windows exe under Git Bash, so
# /tmp/... is invisible to it (tested 22 Aug 2026). cygpath bridges the two worlds.
NATIVE_TMP="$( (command -v cygpath >/dev/null 2>&1 && cygpath -m "${TEMP:-/tmp}") || echo /tmp )"
cat > "$NATIVE_TMP/deadman-test.sh" <<'EOF'
#!/bin/bash
( sleep 120; poweroff ) &
echo "deadman armed" > /var/log/deadman-test.log
EOF

echo "Launching t3.micro with a 120 s deadman…"
IID=$(aws ec2 run-instances --region "$REGION" \
  --image-id "$AMI" --instance-type t3.micro \
  --key-name "$KEY_NAME" --subnet-id "$SUBNET_ID" --security-group-ids "$SG_ID" \
  --instance-initiated-shutdown-behavior terminate \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"DeleteOnTermination":true}}]' \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=latentsky-deadman-test}]' \
  --user-data "file://$NATIVE_TMP/deadman-test.sh" \
  --query 'Instances[0].InstanceId' --output text)
echo "instance: $IID — waiting for it to kill itself (~3 min)…"

VOL=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId' --output text)

aws ec2 wait instance-terminated --region "$REGION" --instance-ids "$IID"
STATE=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
  --query 'Reservations[0].Instances[0].State.Name' --output text)

echo "final state: $STATE"
if [[ "$STATE" != "terminated" ]]; then
  echo "PROBE 5 FAILED — instance is '$STATE', not terminated. DO NOT run a GPU day."
  exit 1
fi

sleep 10
if aws ec2 describe-volumes --region "$REGION" --volume-ids "$VOL" >/dev/null 2>&1; then
  echo "PROBE 5 FAILED — root volume $VOL still exists (DeleteOnTermination did not hold)."
  exit 1
fi

echo "PROBE 5 PASSED — terminated, volume gone. The deadman pattern is safe to trust."
