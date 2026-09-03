#!/bin/bash
# Deploy the daily-run scheduler into the Latent Sky AWS account — three Lambda
# functions, three schedules, one S3 trigger, one alert topic, one secret.
#
#   source ../gpu/latentsky.env && RUNPOD_API_KEY=... ./deploy-daily.sh [--dry-run]
#
# What runs where (all inside the personal account; the GPU is a RunPod pod
# because the account's G-instance quota is 0):
#
#   latentsky-daily-launch   EventBridge, every 20 min 15:05-19:45Z. Waits for the
#                            12Z HRRR analysis and GFS conditioning on NOAA's buckets,
#                            claims the day, then creates the pod with presigned URLs.
#   latentsky-daily-publish  S3 trigger on daily/*/{site.tar.gz,site-verified.tar.gz,
#                            report.html,finished.json}. Unpacks onto the site bucket,
#                            rewrites the catalogue, invalidates, terminates the pod.
#   latentsky-daily-check    EventBridge twice over: mode=reap every 15 minutes (the
#                            only hard bound on GPU spend — RunPod has no pod TTL and
#                            the pod cannot terminate itself), and mode=report once
#                            after the window closes, which emails the alert topic.
#
# ORDER IS DELIBERATE. Both schedules are created DISABLED and enabled only at the
# very end, after the functions, the permissions and the S3 trigger all exist. A
# run that dies half way therefore leaves nothing armed: no launcher firing into a
# system with no publisher and no reaper behind it.
#
# Idempotent: every step creates-or-updates. The RunPod key is written to SSM only
# when the parameter does not exist yet (pass RUNPOD_API_KEY the first time). The
# email subscription needs Kira to click the confirmation link once — the script
# says plainly whether that has happened, because an unconfirmed subscription
# means every alert this system can raise goes nowhere.
#
# Windows: the AWS CLI is a native exe, so file paths use cygpath -m and MSYS path
# conversion is switched off for the whole script (an SSM name like
# /latentsky/... would otherwise arrive as C:/Program Files/Git/latentsky/...).

set -euo pipefail
export MSYS_NO_PATHCONV=1

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown argument: $arg (usage: deploy-daily.sh [--dry-run])" >&2; exit 2 ;;
  esac
done

source "$(dirname "$0")/../gpu/account_guard.sh"
cd "$(dirname "$0")"

REGION=${REGION:-us-east-1}
ACCOUNT=$LATENTSKY_AWS_ACCOUNT
DATA_BUCKET="latentsky-${ACCOUNT}"
SITE_BUCKET="latentsky-site-${ACCOUNT}"
TAG=${TAG:-0.17.0-daily}
IMAGE="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com/latentsky-forecast:${TAG}"
ALERT_EMAIL=${ALERT_EMAIL:-KiraRyan27@gmail.com}
MEMBERS=${MEMBERS:-1}
DAILY_KEEP=${DAILY_KEEP:-7}
CYCLE_HOUR=${CYCLE_HOUR:-12}
NSTEPS=${NSTEPS:-18}
SITE_URL=${SITE_URL:-https://latent-sky.dev}
# The hard spend bound. A single-member run takes ~12 min plus a ~5 min image
# pull; eight members ~30 min. Anything past this is hung, and hung is billed.
if [[ -z "${MAX_POD_MINUTES:-}" ]]; then
  if (( MEMBERS > 1 )); then MAX_POD_MINUTES=75; else MAX_POD_MINUTES=45; fi
fi
KEY_PARAM="/latentsky/runpod-api-key"
ROLE="latentsky-daily-lambda"
TOPIC="latentsky-daily-alerts"
LAUNCH_SCHEDULE="cron(5,25,45 15-19 * * ? *)"
REAP_SCHEDULE="rate(15 minutes)"
REPORT_SCHEDULE="cron(35 20 * * ? *)"

say() { printf '%s\n' "$*"; }
aws_read() { say "+ aws $*" >&2; aws "$@"; }
aws_mutate() {
  if (( DRY_RUN )); then say "[dry-run] aws $*" >&2; return 0; fi
  say "+ aws $*" >&2; aws "$@"
}
NATIVE_TMP="$( (command -v cygpath >/dev/null 2>&1 && cygpath -m "${TEMP:-/tmp}") || echo /tmp )"
HERE_NATIVE="$( (command -v cygpath >/dev/null 2>&1 && cygpath -m "$PWD") || echo "$PWD" )"

# ── 0. Facts the policies need ─────────────────────────────────────────────
say "── distribution"
DIST_ID=$(aws_read cloudfront list-distributions \
  --query "DistributionList.Items[?Comment=='latentsky-site'] | [0].Id" --output text)
[[ -n "$DIST_ID" && "$DIST_ID" != "None" ]] || { say "FATAL: no CloudFront distribution with Comment=latentsky-site (deploy the site first)"; exit 1; }
say "distribution: $DIST_ID"
aws_read ecr describe-images --repository-name latentsky-forecast --region "$REGION" \
  --image-ids imageTag="$TAG" --query 'imageDetails[0].imagePushedAt' --output text >/dev/null \
  || { say "FATAL: image tag $TAG is not in ECR — build it first (DOCKERFILE=Dockerfile.daily)"; exit 1; }

# ── 1. Alert topic + email ─────────────────────────────────────────────────
say "── sns topic $TOPIC"
TOPIC_ARN=$(aws_mutate sns create-topic --name "$TOPIC" --region "$REGION" --query TopicArn --output text)
[[ -n "$TOPIC_ARN" ]] || TOPIC_ARN="arn:aws:sns:${REGION}:${ACCOUNT}:${TOPIC}"
SUB_ARN=$(aws_read sns list-subscriptions-by-topic --topic-arn "$TOPIC_ARN" --region "$REGION" \
  --query "Subscriptions[?Endpoint=='${ALERT_EMAIL}'] | [0].SubscriptionArn" --output text 2>/dev/null || echo "None")
if [[ -z "$SUB_ARN" || "$SUB_ARN" == "None" ]]; then
  aws_mutate sns subscribe --topic-arn "$TOPIC_ARN" --protocol email --notification-endpoint "$ALERT_EMAIL" --region "$REGION" >/dev/null
  SUB_ARN="PendingConfirmation"
fi
# "PendingConfirmation" is not a subscription — it is a subscription that will
# silently drop every alert this system can raise.
if [[ "$SUB_ARN" == "PendingConfirmation" ]]; then
  ALERTS_LIVE="NO — $ALERT_EMAIL has not confirmed. AWS sent a confirmation email; until it is clicked, NO ALERT WILL EVER ARRIVE."
else
  ALERTS_LIVE="yes — $ALERT_EMAIL confirmed"
fi
say "alert delivery: $ALERTS_LIVE"

# ── 2. The RunPod key, once ────────────────────────────────────────────────
say "── ssm parameter $KEY_PARAM"
if aws ssm get-parameter --name "$KEY_PARAM" --region "$REGION" >/dev/null 2>&1; then
  say "parameter exists (not overwritten)"
else
  [[ -n "${RUNPOD_API_KEY:-}" ]] || { say "FATAL: $KEY_PARAM missing and RUNPOD_API_KEY not set"; exit 1; }
  aws_mutate ssm put-parameter --name "$KEY_PARAM" --type SecureString --value "$RUNPOD_API_KEY" --region "$REGION" >/dev/null
  say "parameter written"
fi
KEY_PARAM_ARN="arn:aws:ssm:${REGION}:${ACCOUNT}:parameter${KEY_PARAM}"

# ── 3. IAM role ────────────────────────────────────────────────────────────
say "── iam role $ROLE"
TRUST="$NATIVE_TMP/latentsky-daily-trust.json"
cat > "$TRUST" <<'EOF'
{ "Version": "2012-10-17", "Statement": [ { "Effect": "Allow", "Principal": { "Service": "lambda.amazonaws.com" }, "Action": "sts:AssumeRole" } ] }
EOF
if aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  say "role exists"
else
  aws_mutate iam create-role --role-name "$ROLE" --assume-role-policy-document "file://$TRUST" >/dev/null
  (( DRY_RUN )) || { say "waiting 12 s for the role to propagate"; sleep 12; }
fi
# Attached unconditionally, not only on the create branch: a re-run must repair a
# role whose managed policy was detached by hand.
aws_mutate iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/${ROLE}"
POLICY="$NATIVE_TMP/latentsky-daily-policy.json"
# s3:ListBucket is granted on the BUCKET with NO s3:prefix condition, and that is
# load-bearing rather than lax. HeadObject sends no prefix, so a prefix-conditioned
# ListBucket evaluates to an implicit deny, and S3 then answers a MISSING key with
# 403 instead of 404. Every "has this step happened yet?" check in all three
# functions would raise AccessDenied on a key that simply does not exist, which is
# every check on day one. Verified with `aws iam simulate-custom-policy`, 3 Sep 2026.
# Object reads and writes stay prefix-scoped, which is where the real blast radius is.
cat > "$POLICY" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "DailyObjectsInDataBucket",
      "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": ["arn:aws:s3:::${DATA_BUCKET}/daily/*"] },
    { "Sid": "ExistenceChecksNeedUnconditionedListBucket",
      "Effect": "Allow", "Action": ["s3:ListBucket"],
      "Resource": ["arn:aws:s3:::${DATA_BUCKET}", "arn:aws:s3:::${SITE_BUCKET}"] },
    { "Sid": "SiteWritesLimitedToDailyTreesAndTheCatalogue",
      "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": ["arn:aws:s3:::${SITE_BUCKET}/data/web/daily/*",
                   "arn:aws:s3:::${SITE_BUCKET}/data/web/catalogue.json",
                   "arn:aws:s3:::${SITE_BUCKET}/verification/daily-*"] },
    { "Sid": "InvalidateOnlyThisDistribution",
      "Effect": "Allow", "Action": ["cloudfront:CreateInvalidation"],
      "Resource": ["arn:aws:cloudfront::${ACCOUNT}:distribution/${DIST_ID}"] },
    { "Sid": "ReadTheRunpodKey",
      "Effect": "Allow", "Action": ["ssm:GetParameter"], "Resource": ["${KEY_PARAM_ARN}"] },
    { "Sid": "PublishAlerts",
      "Effect": "Allow", "Action": ["sns:Publish"], "Resource": ["${TOPIC_ARN}"] }
  ]
}
EOF
aws_mutate iam put-role-policy --role-name "$ROLE" --policy-name latentsky-daily --policy-document "file://$POLICY"

# ── 4. Functions ───────────────────────────────────────────────────────────
zip_one() {  # $1 = module basename [$2... extra files] -> prints the native zip path
  local mod=$1 zip="$NATIVE_TMP/latentsky-${mod}.zip"
  shift
  python - "$zip" "$HERE_NATIVE/${mod}.py" "$@" <<'PY'
import sys, zipfile
out, srcs = sys.argv[1], sys.argv[2:]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for src in srcs:
        z.write(src, arcname=src.replace("\\", "/").rsplit("/", 1)[-1])
PY
  echo "$zip"
}

deploy_function() {  # name module timeout memory env_json_file [extra files...]
  local name=$1 mod=$2 timeout=$3 memory=$4 envfile=$5 zip
  shift 5
  zip=$(zip_one "$mod" "$@")
  say "── lambda $name ($mod.handler, ${timeout}s, ${memory} MB)"
  if aws lambda get-function --function-name "$name" --region "$REGION" >/dev/null 2>&1; then
    aws_mutate lambda update-function-code --function-name "$name" --zip-file "fileb://$zip" --region "$REGION" --query 'CodeSha256' --output text
    (( DRY_RUN )) || aws lambda wait function-updated --function-name "$name" --region "$REGION"
    aws_mutate lambda update-function-configuration --function-name "$name" --region "$REGION" \
      --runtime python3.12 --handler "${mod}.handler" --timeout "$timeout" --memory-size "$memory" \
      --environment "file://$envfile" --query 'LastModified' --output text
    (( DRY_RUN )) || aws lambda wait function-updated --function-name "$name" --region "$REGION"
  else
    aws_mutate lambda create-function --function-name "$name" --region "$REGION" \
      --runtime python3.12 --role "$ROLE_ARN" --handler "${mod}.handler" --zip-file "fileb://$zip" \
      --timeout "$timeout" --memory-size "$memory" --environment "file://$envfile" \
      --tags project=latent-sky --query 'FunctionArn' --output text
    (( DRY_RUN )) || aws lambda wait function-active --function-name "$name" --region "$REGION"
  fi
}

ENV_LAUNCH="$NATIVE_TMP/latentsky-env-launch.json"
ENV_PUBLISH="$NATIVE_TMP/latentsky-env-publish.json"
ENV_CHECK="$NATIVE_TMP/latentsky-env-check.json"
cat > "$ENV_LAUNCH" <<EOF
{ "Variables": { "DATA_BUCKET": "${DATA_BUCKET}", "IMAGE": "${IMAGE}", "RUNPOD_KEY_PARAM": "${KEY_PARAM}", "MEMBERS": "${MEMBERS}", "CYCLE_HOUR": "${CYCLE_HOUR}", "NSTEPS": "${NSTEPS}", "MAX_POD_MINUTES": "${MAX_POD_MINUTES}" } }
EOF
cat > "$ENV_PUBLISH" <<EOF
{ "Variables": { "DATA_BUCKET": "${DATA_BUCKET}", "SITE_BUCKET": "${SITE_BUCKET}", "DISTRIBUTION_ID": "${DIST_ID}", "RUNPOD_KEY_PARAM": "${KEY_PARAM}", "DAILY_KEEP": "${DAILY_KEEP}" } }
EOF
cat > "$ENV_CHECK" <<EOF
{ "Variables": { "DATA_BUCKET": "${DATA_BUCKET}", "SITE_BUCKET": "${SITE_BUCKET}", "TOPIC_ARN": "${TOPIC_ARN}", "RUNPOD_KEY_PARAM": "${KEY_PARAM}", "MAX_POD_MINUTES": "${MAX_POD_MINUTES}", "SITE_URL": "${SITE_URL}" } }
EOF
# The launcher ships the current pod script to every pod it starts (hot-patch).
deploy_function latentsky-daily-launch  lambda_launch  180 256  "$ENV_LAUNCH" "$HERE_NATIVE/../../pipeline/pod_daily.sh"
deploy_function latentsky-daily-publish lambda_publish 300 1024 "$ENV_PUBLISH"
deploy_function latentsky-daily-check   lambda_check   180 256  "$ENV_CHECK"

# The launcher spends money, so it gets belt and braces against double-firing:
# one invocation at a time, and no automatic retry of an async invocation that
# already created a pod before failing.
say "── launcher: reserved concurrency 1, zero async retries"
aws_mutate lambda put-function-concurrency --function-name latentsky-daily-launch \
  --reserved-concurrent-executions 1 --region "$REGION" --query 'ReservedConcurrentExecutions' --output text
aws_mutate lambda put-function-event-invoke-config --function-name latentsky-daily-launch \
  --maximum-retry-attempts 0 --maximum-event-age-in-seconds 300 --region "$REGION" \
  --query 'MaximumRetryAttempts' --output text

# ── 5. Schedules, created DISABLED ────────────────────────────────────────
schedule() {  # rule function expression input_json
  local rule=$1 fn=$2 expr=$3 input=${4:-} fn_arn rule_arn targets
  fn_arn="arn:aws:lambda:${REGION}:${ACCOUNT}:function:${fn}"
  say "── events rule $rule: $expr -> $fn ${input:+(input $input)}"
  rule_arn=$(aws_mutate events put-rule --name "$rule" --schedule-expression "$expr" --state DISABLED \
    --description "Latent Sky daily run" --region "$REGION" --query RuleArn --output text)
  [[ -n "$rule_arn" ]] || rule_arn="arn:aws:events:${REGION}:${ACCOUNT}:rule/${rule}"
  targets="$NATIVE_TMP/latentsky-targets-${rule}.json"
  python - "$targets" "$fn" "$fn_arn" "$input" <<'PY'
import json, sys
out, fid, arn, inp = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
t = {"Id": fid, "Arn": arn}
if inp:
    t["Input"] = inp
json.dump([t], open(out, "w"))
PY
  aws_mutate events put-targets --rule "$rule" --region "$REGION" --targets "file://$targets" \
    --query FailedEntryCount --output text
  aws lambda add-permission --function-name "$fn" --region "$REGION" --statement-id "events-${rule}" \
    --action lambda:InvokeFunction --principal events.amazonaws.com --source-arn "$rule_arn" >/dev/null 2>&1 \
    || say "(invoke permission for $rule already present)"
}
schedule latentsky-daily-launch latentsky-daily-launch "$LAUNCH_SCHEDULE"
schedule latentsky-daily-reap   latentsky-daily-check  "$REAP_SCHEDULE"   '{"mode":"reap"}'
schedule latentsky-daily-report latentsky-daily-check  "$REPORT_SCHEDULE" '{"mode":"report"}'

# ── 6. S3 trigger for the publisher ───────────────────────────────────────
say "── s3 notifications on $DATA_BUCKET (daily/* -> latentsky-daily-publish)"
PUBLISH_ARN="arn:aws:lambda:${REGION}:${ACCOUNT}:function:latentsky-daily-publish"
aws lambda add-permission --function-name latentsky-daily-publish --region "$REGION" --statement-id s3-daily \
  --action lambda:InvokeFunction --principal s3.amazonaws.com --source-arn "arn:aws:s3:::${DATA_BUCKET}" \
  --source-account "$ACCOUNT" >/dev/null 2>&1 || say "(s3 invoke permission already present)"
EXISTING=$(aws_read s3api get-bucket-notification-configuration --bucket "$DATA_BUCKET" --region "$REGION" --output json)
# The CLI prints NOTHING (not "{}") for a bucket with no notification config, so
# an emptiness test must accept both. Comparing only against "{}" made the very
# first deploy refuse to continue (reproduced 3 Sep 2026).
EXISTING_TRIMMED="$(printf '%s' "$EXISTING" | tr -d '[:space:]')"
if [[ -n "$EXISTING_TRIMMED" && "$EXISTING_TRIMMED" != "{}" ]] && ! grep -q "latentsky-daily-publish" <<<"$EXISTING"; then
  say "REFUSING: $DATA_BUCKET already has a notification configuration that is not ours:"
  say "$EXISTING"
  exit 1
fi
NOTIF="$NATIVE_TMP/latentsky-daily-notif.json"
python - "$NOTIF" "$PUBLISH_ARN" <<'PY'
import json, sys
out, arn = sys.argv[1], sys.argv[2]
cfg = {"LambdaFunctionConfigurations": [
    {"Id": f"latentsky-daily-{suffix.replace('.', '-')}", "LambdaFunctionArn": arn, "Events": ["s3:ObjectCreated:*"],
     "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "daily/"}, {"Name": "suffix", "Value": suffix}]}}}
    for suffix in ("site.tar.gz", "site-verified.tar.gz", "report.html", "finished.json")]}
json.dump(cfg, open(out, "w"), indent=1)
PY
aws_mutate s3api put-bucket-notification-configuration --bucket "$DATA_BUCKET" --region "$REGION" \
  --notification-configuration "file://$NOTIF"

# ── 7. Arm the schedules, last ────────────────────────────────────────────
# Everything the schedules depend on now exists. Enabling here means a run that
# failed earlier leaves the system inert rather than half-armed.
say "── enabling schedules"
for rule in latentsky-daily-launch latentsky-daily-reap latentsky-daily-report; do
  aws_mutate events enable-rule --name "$rule" --region "$REGION"
done

say ""
say "daily scheduler deployed."
say "  launch   $LAUNCH_SCHEDULE   (cycle ${CYCLE_HOUR}Z, +${NSTEPS} h, members=$MEMBERS, image $TAG)"
say "  reap     $REAP_SCHEDULE     (terminates any daily pod over $MAX_POD_MINUTES min — the spend bound)"
say "  report   $REPORT_SCHEDULE   -> $TOPIC_ARN"
say "  alerts   $ALERTS_LIVE"
say ""
say "  Would today launch, without creating anything or spending a cent:"
say "    aws lambda invoke --function-name latentsky-daily-launch --region $REGION \\"
say "      --cli-binary-format raw-in-base64-out --payload '{\"check_only\":true}' /dev/stdout"
say ""
say "  Launch a REAL pod for a past day (costs roughly \$0.50 and puts that day on the live site):"
say "    aws lambda invoke --function-name latentsky-daily-launch --region $REGION \\"
say "      --cli-binary-format raw-in-base64-out --payload '{\"date\":\"YYYY-MM-DD\"}' /dev/stdout"
