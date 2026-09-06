#!/bin/bash
# Turn on CloudFront access logs for latent-sky.dev — the ground truth of who visits.
#
#   source ../gpu/latentsky.env && ./setup-analytics.sh            # create/ensure everything
#   source ../gpu/latentsky.env && ./setup-analytics.sh --status   # read-only: what exists, newest log objects
#
# What this builds (idempotent; every step is create-or-confirm):
#
#   1. bucket    latentsky-logs-$LATENTSKY_AWS_ACCOUNT — private, all four public-access
#                blocks on, objects expire after LOG_RETENTION_DAYS (raw logs hold IP
#                addresses, so they are not kept forever), incomplete uploads swept.
#   2. policy    lets the CloudWatch Logs delivery service (delivery.logs.amazonaws.com)
#                write into it, and ONLY when the request comes from this account's own
#                delivery sources — the aws:SourceAccount / aws:SourceArn conditions are
#                what stop any other account's delivery pointing at the bucket.
#   3. delivery  CloudFront "standard logging (v2)": a delivery SOURCE bound to the
#                distribution, a delivery DESTINATION bound to the bucket, and the
#                DELIVERY joining them with an explicit field list. Chosen over the legacy
#                per-distribution logging because it needs no bucket ACLs (the legacy
#                path requires ACLs to be re-enabled and a canonical-user grant), lets us
#                pick the fields, partitions objects by date, and can record the viewer's
#                country and ASN, which the legacy format never had.
#
# The page itself is untouched: nothing here runs in a browser, sets a cookie, or can be
# blocked. The optional Cloudflare beacon (a dashboard, ~a third of technical visitors
# block it) is a separate, deploy-time concern in deploy-site.sh.
#
# Reading the logs: infra/web/visitors.py summarises a day. Object layout under the
# bucket is cloudfront/<DistributionId>/<yyyy>/<MM>/<dd>/<HH>/… as set by SUFFIX below.
#
# Cost: CloudWatch vended-log delivery to S3 is billed per GB delivered (cents per GB);
# this site's traffic is megabytes a day. Storage is pennies.
#
# NOT DONE HERE: nothing in the distribution config changes (v2 logging lives entirely
# in the Logs delivery API), so the legacy `Logging` block stays disabled.

set -euo pipefail

MODE=apply
for arg in "$@"; do
  case "$arg" in
    --status) MODE=status ;;
    *) echo "unknown argument: $arg (usage: setup-analytics.sh [--status])" >&2; exit 2 ;;
  esac
done

source "$(dirname "$0")/../gpu/account_guard.sh"
cd "$(dirname "$0")"

REGION=${REGION:-us-east-1}          # CloudFront is global; its log deliveries live in us-east-1
LOG_BUCKET="latentsky-logs-${LATENTSKY_AWS_ACCOUNT}"
LOG_RETENTION_DAYS=${LOG_RETENTION_DAYS:-180}
SITE_HOST=${SITE_HOST:-latent-sky.dev}
SOURCE_NAME="latentsky-site-access"
DEST_NAME="latentsky-site-logs-s3"
SUFFIX='cloudfront/{DistributionId}/{yyyy}/{MM}/{dd}/{HH}'

# The field list. c-country and asn are v2-only; if the API rejects them we fall back to
# the classic set and say so, rather than failing the whole setup on two columns.
FIELDS_FULL=(timestamp\(ms\) c-ip c-country asn cs-method cs-uri-stem cs-uri-query sc-status
             cs\(Referer\) cs\(User-Agent\) x-edge-result-type x-edge-location sc-bytes
             time-taken cs-protocol-version x-host-header)
FIELDS_BASE=(timestamp\(ms\) c-ip cs-method cs-uri-stem cs-uri-query sc-status
             cs\(Referer\) cs\(User-Agent\) x-edge-result-type x-edge-location sc-bytes
             time-taken cs-protocol-version x-host-header)

say() { printf '%s\n' "$*"; }
NATIVE_TMP="$(cygpath -m "${TEMP:-/tmp}" 2>/dev/null || echo "${TEMP:-/tmp}")"

# ── The distribution ──────────────────────────────────────────────────────────
DIST_ID=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?contains(Aliases.Items, '${SITE_HOST}')].Id | [0]" --output text)
if [[ -z "$DIST_ID" || "$DIST_ID" == "None" ]]; then
  say "REFUSING: no CloudFront distribution carries the alias ${SITE_HOST}"; exit 1
fi
DIST_ARN="arn:aws:cloudfront::${LATENTSKY_AWS_ACCOUNT}:distribution/${DIST_ID}"
say "distribution: $DIST_ID"

# ── --status: read-only ───────────────────────────────────────────────────────
if [[ "$MODE" == status ]]; then
  say "── bucket"
  if aws s3api head-bucket --bucket "$LOG_BUCKET" 2>/dev/null; then
    say "  $LOG_BUCKET exists"
    say "  retention: $(aws s3api get-bucket-lifecycle-configuration --bucket "$LOG_BUCKET" \
         --query 'Rules[?ID==`expire-access-logs`].Expiration.Days | [0]' --output text 2>/dev/null || echo none) days"
  else
    say "  $LOG_BUCKET does not exist"
  fi
  say "── delivery"
  aws logs describe-deliveries --region "$REGION" \
    --query "deliveries[?deliverySourceName=='${SOURCE_NAME}'].{id:id,dest:deliveryDestinationArn,fields:recordFields}" --output json
  say "── newest log objects"
  aws s3api list-objects-v2 --bucket "$LOG_BUCKET" --prefix "cloudfront/${DIST_ID}/" \
    --query 'sort_by(Contents, &LastModified)[-8:].[LastModified, Size, Key]' --output text 2>/dev/null \
    | sed 's/^/  /' || say "  (none yet)"
  exit 0
fi

# ── 1. Bucket ─────────────────────────────────────────────────────────────────
say "── bucket $LOG_BUCKET"
if aws s3api head-bucket --bucket "$LOG_BUCKET" 2>/dev/null; then
  say "  exists"
else
  aws s3api create-bucket --bucket "$LOG_BUCKET" --region "$REGION" >/dev/null
  say "  created"
fi
aws s3api put-public-access-block --bucket "$LOG_BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-ownership-controls --bucket "$LOG_BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'
cat > "$NATIVE_TMP/latentsky-log-lifecycle.json" <<JSON
{"Rules": [
  {"ID": "expire-access-logs", "Status": "Enabled", "Filter": {"Prefix": ""},
   "Expiration": {"Days": ${LOG_RETENTION_DAYS}},
   "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7}}
]}
JSON
aws s3api put-bucket-lifecycle-configuration --bucket "$LOG_BUCKET" \
  --lifecycle-configuration "file://$NATIVE_TMP/latentsky-log-lifecycle.json"
say "  private, public access blocked, objects expire after ${LOG_RETENTION_DAYS} days"

# ── 2. Bucket policy for the delivery service ─────────────────────────────────
cat > "$NATIVE_TMP/latentsky-log-policy.json" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AWSLogDeliveryWrite",
      "Effect": "Allow",
      "Principal": {"Service": "delivery.logs.amazonaws.com"},
      "Action": "s3:PutObject",
      "Resource": "arn:aws:s3:::${LOG_BUCKET}/*",
      "Condition": {
        "StringEquals": {
          "s3:x-amz-acl": "bucket-owner-full-control",
          "aws:SourceAccount": "${LATENTSKY_AWS_ACCOUNT}"
        },
        "ArnLike": {"aws:SourceArn": "arn:aws:logs:${REGION}:${LATENTSKY_AWS_ACCOUNT}:delivery-source:*"}
      }
    },
    {
      "Sid": "AWSLogDeliveryAclCheck",
      "Effect": "Allow",
      "Principal": {"Service": "delivery.logs.amazonaws.com"},
      "Action": "s3:GetBucketAcl",
      "Resource": "arn:aws:s3:::${LOG_BUCKET}",
      "Condition": {
        "StringEquals": {"aws:SourceAccount": "${LATENTSKY_AWS_ACCOUNT}"},
        "ArnLike": {"aws:SourceArn": "arn:aws:logs:${REGION}:${LATENTSKY_AWS_ACCOUNT}:delivery-source:*"}
      }
    }
  ]
}
JSON
aws s3api put-bucket-policy --bucket "$LOG_BUCKET" --policy "file://$NATIVE_TMP/latentsky-log-policy.json"
say "  policy: delivery.logs.amazonaws.com may write, from this account's delivery sources only"

# ── 3. Delivery source → destination → delivery ───────────────────────────────
say "── delivery source $SOURCE_NAME"
aws logs put-delivery-source --region "$REGION" --name "$SOURCE_NAME" \
  --resource-arn "$DIST_ARN" --log-type ACCESS_LOGS >/dev/null
say "  bound to $DIST_ID (ACCESS_LOGS)"

say "── delivery destination $DEST_NAME"
DEST_ARN=$(aws logs put-delivery-destination --region "$REGION" --name "$DEST_NAME" \
  --output-format json \
  --delivery-destination-configuration "destinationResourceArn=arn:aws:s3:::${LOG_BUCKET}" \
  --query 'deliveryDestination.arn' --output text)
say "  $DEST_ARN (json lines)"

say "── delivery"
EXISTING=$(aws logs describe-deliveries --region "$REGION" \
  --query "deliveries[?deliverySourceName=='${SOURCE_NAME}'].id | [0]" --output text)
if [[ -n "$EXISTING" && "$EXISTING" != "None" ]]; then
  say "  exists: $EXISTING"
  aws logs describe-deliveries --region "$REGION" \
    --query "deliveries[?id=='${EXISTING}'].{fields:recordFields,s3:s3DeliveryConfiguration}" --output json
else
  create() {
    # JSON, not shorthand: the CLI's shorthand parser reads the {braces} of the
    # path template as its own syntax and refuses the whole argument.
    aws logs create-delivery --region "$REGION" \
      --delivery-source-name "$SOURCE_NAME" --delivery-destination-arn "$DEST_ARN" \
      --record-fields "$@" \
      --s3-delivery-configuration "{\"suffixPath\": \"${SUFFIX}\", \"enableHiveCompatiblePath\": false}" \
      --query 'delivery.id' --output text
  }
  if ID=$(create "${FIELDS_FULL[@]}" 2>"$NATIVE_TMP/latentsky-log-create.err"); then
    say "  created $ID with country + ASN fields"
  else
    say "  the full field list was rejected:"; sed 's/^/    /' "$NATIVE_TMP/latentsky-log-create.err"
    say "  retrying with the classic fields (no c-country / asn; visitors.py falls back to the edge location)"
    ID=$(create "${FIELDS_BASE[@]}")
    say "  created $ID"
  fi
fi

say
say "access logging is ON. Objects arrive within minutes to an hour, under:"
say "  s3://${LOG_BUCKET}/cloudfront/${DIST_ID}/<yyyy>/<MM>/<dd>/<HH>/"
say "Summarise a day with:  python visitors.py --date YYYY-MM-DD"
