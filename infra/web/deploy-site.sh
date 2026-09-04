#!/bin/bash
# Deploy the Latent Sky site — private S3 + CloudFront (OAC), per Architecture.md §9.2.
#
#   source ../gpu/latentsky.env && ./deploy-site.sh [--dry-run]
#
# Idempotent steps, in order:
#   1. preflight   — web/dist exists and is structurally sound; every file has a known
#                    Content-Type; data/web/manifest.json exists
#   2. stage       — copy ../../data/web into dist/data/web, then a licence tripwire
#                    audits the staged manifest (see LICENCE GUARD below)
#   3. bucket      — create/ensure latentsky-site-$LATENTSKY_AWS_ACCOUNT, private,
#                    all four public-access blocks on
#   4. cloudfront  — create/ensure an Origin Access Control (OAC — OAI is legacy and
#                    unsupported on flat-rate plans) and a distribution: S3 REST origin
#                    (never the website endpoint), default root index.html, http2and3,
#                    Compress on, PriceClass_100, 403/404 -> /index.html 200 for SPA routes
#   5. policy      — bucket policy allowing s3:GetObject to cloudfront.amazonaws.com
#                    ONLY when AWS:SourceArn is this distribution
#   6. upload      — full upload (never sync) with Cache-Control and Content-Type baked
#                    into S3 object metadata (response-headers policies are Business-tier):
#                      /assets/* /cesium/* /data/*  ->  public, max-age=31536000, immutable
#                      /index.html, /data/web/manifest.json  ->  no-cache
#   7. invalidate  — /index.html and /data/web/manifest.json ONLY. Never /*.
#   8. verify      — curl the distribution for index.html, manifest.json, one webp frame;
#                    assert status, Content-Type, Cache-Control, and that CloudFront
#                    actually compresses index.html (§9.2: a missing Content-Encoding
#                    costs every visitor ~3x the bytes and nothing anywhere reports it)
#
# WHY THE DEPLOY SCRIPT (not the vite build) COMPLETES dist/ WITH /data/web:
#   In dev, a vite middleware serves /data/* straight from ../data at runtime; the build
#   itself never touches data (vite-plugin-static-copy only copies the cesium runtime
#   tree). Kept that way deliberately:
#     - CI (web.yml) builds without pipeline output present; a build-time data copy
#       would either fail there or need its own conditional.
#     - data/ also holds data/dev/** — CC BY-NC-ND-derived, a licence violation to
#       publish. No build-time glob gets the chance to sweep it up: this script copies
#       exactly one directory, data/web, and then audits what it staged.
#   So "dist/ ends complete" here: stage data/web -> dist/data/web, upload one tree.
#
# LICENCE GUARD: refuses to upload if the staged manifest references any file outside
#   data/web, any missing file, or any CWB/data-dev tell-tale (the CorrDiff package's
#   bundled sample dataset is CC BY-NC-ND 4.0 — data derived from it must never ship).
#   Over-triggering is intended; if a legitimate manifest ever trips it, edit the guard
#   knowingly rather than routing around it.
#
# COMPRESSION RULES (§9.2): CloudFront auto-compresses only 1,000–10,000,000-byte
#   objects and only whitelisted content types — application/octet-stream is not on the
#   list. Every text asset here (html, js, css, json, wasm) lands in that window with a
#   correct Content-Type (largest: the ~3.9 MB cesium chunk), so Compress=true covers
#   them; nothing is uploaded as octet-stream. WebP is compressed by format and ships
#   identity. Step 8 verifies the header instead of hoping.
#
# WHY `aws s3 cp --recursive` AND NEVER `aws s3 sync`: sync applies metadata only to
#   objects it actually transfers — re-running with corrected headers over unchanged
#   objects is a silent no-op (§9.2). The whole site is ~10 MB; re-uploading everything
#   makes the metadata deterministic on every run. No --delete: superseded hashed
#   assets linger harmlessly (immutable, unreferenced) and entry points are no-cache.
#
# NOT RECONCILED: config drift on an existing distribution (this script creates it
#   correctly and thereafter only reads its Id/DomainName). Change config in the
#   console/CLI deliberately, or delete the distribution and re-run.
#
# --dry-run: every read-only aws call (list/get/describe/head) runs and is printed with
#   a leading "+"; every mutating call is printed with "[dry-run]" and NOT executed;
#   verification curls are printed and skipped. Local staging still happens (it is what
#   would be uploaded). Safety: account guard, terminate-on-mismatch, as everywhere.

set -euo pipefail

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown argument: $arg (usage: deploy-site.sh [--dry-run])" >&2; exit 2 ;;
  esac
done

source "$(dirname "$0")/../gpu/account_guard.sh"
cd "$(dirname "$0")"

REGION=${REGION:-us-east-1}
SITE_BUCKET="latentsky-site-${LATENTSKY_AWS_ACCOUNT}"
NATIVE_TMP_EARLY="$( (command -v cygpath >/dev/null 2>&1 && cygpath -m "${TEMP:-/tmp}") || echo /tmp )"
ORIGIN_DOMAIN="${SITE_BUCKET}.s3.${REGION}.amazonaws.com"   # S3 REST endpoint — never -website
OAC_NAME="latentsky-site-oac"
DIST_COMMENT="latentsky-site"
REPO="$(cd ../.. && pwd)"
DIST="$REPO/web/dist"
DATA_SRC="$REPO/data/web"

IMMUTABLE_CC="public, max-age=31536000, immutable"
NOCACHE_CC="no-cache"
ROOT_CC="public, max-age=86400"    # unhashed root extras (og.png): cacheable, not immutable

say() { printf '%s\n' "$*"; }

# Read-only AWS call: echoes the command (stderr), always executes, stdout is the result.
aws_read() {
  say "+ aws $*" >&2
  aws "$@"
}

# Mutating AWS call: echoes the command; executes only outside --dry-run.
aws_mutate() {
  if (( DRY_RUN )); then
    say "[dry-run] aws $*" >&2
    return 0
  fi
  say "+ aws $*" >&2
  aws "$@"
}

content_type_for() {
  case "$1" in
    js|mjs)  echo "text/javascript" ;;
    css)     echo "text/css" ;;
    html)    echo "text/html" ;;
    json|map) echo "application/json" ;;
    webp)    echo "image/webp" ;;
    png)     echo "image/png" ;;
    jpg|jpeg) echo "image/jpeg" ;;
    gif)     echo "image/gif" ;;
    svg)     echo "image/svg+xml" ;;
    wasm)    echo "application/wasm" ;;
    xml)     echo "application/xml" ;;
    txt)     echo "text/plain" ;;
    ico)     echo "image/x-icon" ;;
    ktx2)    echo "image/ktx2" ;;
    glb)     echo "model/gltf-binary" ;;
    terrain) echo "application/octet-stream" ;;
    woff2)   echo "font/woff2" ;;
    woff)    echo "font/woff" ;;
    *)       return 1 ;;
  esac
}

# ── 1. Preflight ───────────────────────────────────────────────────────────────
say "── preflight: $DIST"
[[ -f "$DIST/index.html" ]] || { say "REFUSING: $DIST/index.html missing — run \`make build\` first"; exit 1; }
# Guard against the vite-plugin-static-copy v4 path regression (files nested under
# dest/node_modules/…): the runtime tree must sit exactly at cesium/Workers etc.
ls "$DIST"/cesium/Workers/*.js >/dev/null 2>&1 \
  || { say "REFUSING: dist/cesium/Workers/*.js missing — the cesium runtime tree is misplaced; rebuild with the stripBase fix in web/vite.config.ts"; exit 1; }
[[ -d "$DIST/cesium/Workers/node_modules" || -d "$DIST/cesium/Assets/node_modules" ]] \
  && { say "REFUSING: dist/cesium contains a nested node_modules path — stale broken build; rm -rf web/dist and rebuild"; exit 1; }
[[ -f "$DATA_SRC/manifest.json" ]] || { say "REFUSING: $DATA_SRC/manifest.json missing — nothing to deploy under /data/web"; exit 1; }

# ── 2. Stage data/web into dist, then the licence tripwire ─────────────────────
say "── staging $DATA_SRC -> $DIST/data/web"
rm -rf "$DIST/data"
mkdir -p "$DIST/data"
cp -R "$DATA_SRC" "$DIST/data/web"
# Daily runs are owned by the bucket, not the repo: infra/daily publishes them
# from the pod, and a local copy only exists because the browser tests need one.
# Staging it would re-upload megabytes this deploy did not produce and could
# overwrite a fresher tree with a stale checkout.
if [[ -d "$DIST/data/web/daily" ]]; then
  say "  (not staging data/web/daily — the daily runs live in the bucket)"
  rm -rf "$DIST/data/web/daily"
fi

# The merge with the live catalogue's daily runs happens LATER, immediately
# before the catalogue is uploaded — see "merge the live daily runs" below. Doing
# it here would open a window the length of the whole deploy in which a daily
# publish could land and be overwritten.

# Verification reports are standalone HTML pages (pipeline/tools/fss_report.py
# --site-out) served at /verification/<name>.html. They change whenever a run is
# re-scored, so they are no-cache like the entry points, never immutable.
PAGES_SRC="$REPO/data/verification/pages"
rm -rf "$DIST/verification"
if [[ -d "$PAGES_SRC" ]]; then
  say "── staging $PAGES_SRC -> $DIST/verification"
  mkdir -p "$DIST/verification"
  cp "$PAGES_SRC"/*.html "$DIST/verification/"
fi

python - "$DIST/data/web" <<'PYGUARD'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()

# Audit EVERY manifest the site can load, not just the root one. Since the
# multi-event catalogue landed, most of the payload lives in event subtrees
# (data/web/taiwan/...), and a guard reading only manifest.json would wave all
# of it through unexamined — the opposite of what a tripwire is for. Paths in
# each manifest resolve against ITS OWN directory, exactly as the browser
# resolves them against the manifest URL.
targets = [pathlib.Path("manifest.json")]
catalogue = root / "catalogue.json"
skipped = []
if catalogue.is_file():
    for event in json.loads(catalogue.read_text(encoding="utf-8")).get("events", []):
        rel = pathlib.Path(event["manifest"])
        # Daily runs are published straight into the bucket by infra/daily and are
        # never staged here; their manifests were schema-validated by the encoder
        # in the pod, and nothing NC-ND can reach that path.
        if event["id"].startswith("daily-"):
            skipped.append(event["id"])
            continue
        if rel not in targets:
            targets.append(rel)
if skipped:
    print(f"daily runs not audited (live only): {skipped}")

problems, summary = [], []
for rel in targets:
    mp = (root / rel).resolve()
    if not mp.is_relative_to(root):
        problems.append(f"{rel}: manifest path escapes data/web")
        continue
    if not mp.is_file():
        problems.append(f"{rel}: manifest referenced by the catalogue does not exist")
        continue
    m = json.loads(mp.read_text(encoding="utf-8"))
    base = mp.parent
    refs = []
    for layer in m["layers"].values():
        refs.extend(layer["frames"])
        refs.append(layer["lut"])
    basemap = m.get("basemap") or {}
    for key in ("global", "hero"):
        if basemap.get(key):
            refs.append(basemap[key])
    for ref in refs:
        p = (base / ref).resolve()
        if not p.is_relative_to(root):
            problems.append(f"{rel} -> {ref}: escapes data/web")
        elif not p.is_file():
            problems.append(f"{rel} -> {ref}: referenced file missing")
    blob = json.dumps(m).lower()
    for telltale in ("cwb", "data/dev"):
        if telltale in blob:
            problems.append(
                f"{rel} contains {telltale!r} — CC BY-NC-ND-derived dev data must never deploy"
            )
    summary.append(
        f"  {str(rel):28} run.id={m['run']['id']} kind={m['run']['kind']} "
        f"layers={len(m['layers'])} frames={len(m['frames'])} refs={len(refs)}"
    )

if problems:
    for p in problems:
        print(f"REFUSING (licence/integrity guard): {p}")
    sys.exit(1)
print(f"manifests OK ({len(targets)} audited):")
for line in summary:
    print(line)
PYGUARD

# Every file to be uploaded must have a mapped Content-Type — audit BEFORE any mutation.
while IFS= read -r -d '' f; do
  name="${f##*/}"
  [[ "$name" == *.* ]] || { say "REFUSING: $f has no extension — cannot assign Content-Type"; exit 1; }
  ext="$(printf '%s' "${name##*.}" | tr '[:upper:]' '[:lower:]')"
  content_type_for "$ext" >/dev/null \
    || { say "REFUSING: no Content-Type mapping for .$ext ($f) — add it to content_type_for"; exit 1; }
done < <(find "$DIST" -type f -print0)
say "content-type audit OK: $(find "$DIST" -type f | wc -l | tr -d ' ') files"

# ── 3. Site bucket (private, public access blocked) ────────────────────────────
say "── bucket: s3://$SITE_BUCKET ($REGION)"
say "+ aws s3api head-bucket --bucket $SITE_BUCKET --region $REGION (existence check)" >&2
if aws s3api head-bucket --bucket "$SITE_BUCKET" --region "$REGION" >/dev/null 2>&1; then
  say "bucket exists"
else
  if [[ "$REGION" == "us-east-1" ]]; then
    aws_mutate s3api create-bucket --bucket "$SITE_BUCKET" --region "$REGION"
  else
    aws_mutate s3api create-bucket --bucket "$SITE_BUCKET" --region "$REGION" \
      --create-bucket-configuration "LocationConstraint=$REGION"
  fi
fi
aws_mutate s3api put-public-access-block --bucket "$SITE_BUCKET" --region "$REGION" \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# ── 4. CloudFront: OAC + distribution ──────────────────────────────────────────
say "── origin access control: $OAC_NAME"
OAC_ID=$(aws_read cloudfront list-origin-access-controls \
  --query "OriginAccessControlList.Items[?Name=='$OAC_NAME'] | [0].Id" --output text)
if [[ -z "$OAC_ID" || "$OAC_ID" == "None" ]]; then
  OAC_ID=$(aws_mutate cloudfront create-origin-access-control \
    --origin-access-control-config \
    "Name=$OAC_NAME,Description=Latent Sky site bucket access,SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3" \
    --query "OriginAccessControl.Id" --output text)
  if [[ -z "$OAC_ID" ]]; then
    (( DRY_RUN )) || { say "FATAL: create-origin-access-control returned no id"; exit 1; }
    OAC_ID="DRYRUN-OAC-ID"
  fi
fi
say "OAC id: $OAC_ID"

# Managed cache policy Managed-CachingOptimized (honours origin Cache-Control; br+gzip
# in the cache key, which Compress=true needs) — looked up, never hardcoded.
CACHE_POLICY_ID=$(aws_read cloudfront list-cache-policies --type managed \
  --query "CachePolicyList.Items[?CachePolicy.CachePolicyConfig.Name=='Managed-CachingOptimized'] | [0].CachePolicy.Id" \
  --output text)
[[ -n "$CACHE_POLICY_ID" && "$CACHE_POLICY_ID" != "None" ]] \
  || { say "FATAL: managed cache policy Managed-CachingOptimized not found"; exit 1; }

say "── distribution (Comment='$DIST_COMMENT')"
DIST_ID=$(aws_read cloudfront list-distributions \
  --query "DistributionList.Items[?Comment=='$DIST_COMMENT'] | [0].Id" --output text)
if [[ -z "$DIST_ID" || "$DIST_ID" == "None" ]]; then
  # Native-visible temp path: the AWS CLI is a Windows exe under Git Bash, so /tmp/…
  # is invisible to it — cygpath bridges the two worlds (same trick as build-image-remote.sh).
  NATIVE_TMP="$( (command -v cygpath >/dev/null 2>&1 && cygpath -m "${TEMP:-/tmp}") || echo /tmp )"
  DIST_CONFIG="$NATIVE_TMP/latentsky-site-distconfig.json"
  cat > "$DIST_CONFIG" <<EOF
{
  "DistributionConfig": {
    "CallerReference": "latentsky-site-${LATENTSKY_AWS_ACCOUNT}",
    "Comment": "${DIST_COMMENT}",
    "Enabled": true,
    "DefaultRootObject": "index.html",
    "HttpVersion": "http2and3",
    "IsIPV6Enabled": true,
    "PriceClass": "PriceClass_100",
    "Origins": {
      "Quantity": 1,
      "Items": [
        {
          "Id": "s3-rest-origin",
          "DomainName": "${ORIGIN_DOMAIN}",
          "OriginAccessControlId": "${OAC_ID}",
          "S3OriginConfig": { "OriginAccessIdentity": "" }
        }
      ]
    },
    "DefaultCacheBehavior": {
      "TargetOriginId": "s3-rest-origin",
      "ViewerProtocolPolicy": "redirect-to-https",
      "Compress": true,
      "CachePolicyId": "${CACHE_POLICY_ID}",
      "AllowedMethods": {
        "Quantity": 2,
        "Items": ["GET", "HEAD"],
        "CachedMethods": { "Quantity": 2, "Items": ["GET", "HEAD"] }
      }
    },
    "CustomErrorResponses": {
      "Quantity": 2,
      "Items": [
        { "ErrorCode": 403, "ResponsePagePath": "/index.html", "ResponseCode": "200", "ErrorCachingMinTTL": 10 },
        { "ErrorCode": 404, "ResponsePagePath": "/index.html", "ResponseCode": "200", "ErrorCachingMinTTL": 10 }
      ]
    }
  },
  "Tags": { "Items": [ { "Key": "project", "Value": "latent-sky" } ] }
}
EOF
  say "distribution config written to $DIST_CONFIG:"
  cat "$DIST_CONFIG"
  DIST_ID=$(aws_mutate cloudfront create-distribution-with-tags \
    --distribution-config-with-tags "file://$DIST_CONFIG" \
    --query "Distribution.Id" --output text)
  if [[ -z "$DIST_ID" ]]; then
    (( DRY_RUN )) || { say "FATAL: create-distribution-with-tags returned no id"; exit 1; }
    DIST_ID="DRYRUN-DIST-ID"
  fi
  say "created distribution $DIST_ID — waiting until deployed (first deploy takes minutes)…"
  aws_mutate cloudfront wait distribution-deployed --id "$DIST_ID"
else
  say "distribution exists: $DIST_ID"
fi

if (( DRY_RUN )) && [[ "$DIST_ID" == "DRYRUN-DIST-ID" ]]; then
  DOMAIN="DRYRUN.cloudfront.net"
else
  DOMAIN=$(aws_read cloudfront get-distribution --id "$DIST_ID" \
    --query "Distribution.DomainName" --output text)
fi
DIST_ARN="arn:aws:cloudfront::${LATENTSKY_AWS_ACCOUNT}:distribution/${DIST_ID}"
say "distribution domain: $DOMAIN"

# ── 5. Bucket policy: this distribution only, via AWS:SourceArn ────────────────
say "── bucket policy (reads allowed only from $DIST_ARN)"
NATIVE_TMP="${NATIVE_TMP:-$( (command -v cygpath >/dev/null 2>&1 && cygpath -m "${TEMP:-/tmp}") || echo /tmp )}"
POLICY_FILE="$NATIVE_TMP/latentsky-site-bucket-policy.json"
cat > "$POLICY_FILE" <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowCloudFrontServicePrincipalReadOnly",
      "Effect": "Allow",
      "Principal": { "Service": "cloudfront.amazonaws.com" },
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::${SITE_BUCKET}/*",
      "Condition": { "StringEquals": { "AWS:SourceArn": "${DIST_ARN}" } }
    }
  ]
}
EOF
say "bucket policy written to $POLICY_FILE:"
cat "$POLICY_FILE"
aws_mutate s3api put-bucket-policy --bucket "$SITE_BUCKET" --region "$REGION" \
  --policy "file://$POLICY_FILE"

# ── 6. Upload — Cache-Control and Content-Type baked into object metadata ──────
# Immutable trees first (one cp per extension so metadata is explicit, never guessed
# by the CLI), then root files, then the two no-cache entry points LAST so their
# metadata always wins.
upload_tree() {  # $1 = subdir under dist, $2 = Cache-Control, $3... = extra --exclude globs
  local sub=$1 cc=$2 ext ctype
  shift 2
  local skips=()
  for g in "$@"; do skips+=(--exclude "$g"); done
  local exts
  exts=$(find "$DIST/$sub" -type f -name '*.*' | sed 's/.*\.//' | tr '[:upper:]' '[:lower:]' | sort -u)
  for ext in $exts; do
    ctype=$(content_type_for "$ext")   # audited in preflight — cannot fail here
    # The extra excludes come AFTER the include so they win: aws applies filters
    # in order and the last match decides.
    aws_mutate s3 cp "$DIST/$sub" "s3://$SITE_BUCKET/$sub" --region "$REGION" \
      --recursive --exclude "*" --include "*.$ext" "${skips[@]}" \
      --content-type "$ctype" --cache-control "$cc" --only-show-errors
  done
}

say "── upload: immutable trees"
upload_tree assets "$IMMUTABLE_CC"
upload_tree cesium "$IMMUTABLE_CC"
# EVERY entry point is excluded here and uploaded later with no-cache. Two
# reasons, the second learned the hard way on 4 Sep 2026:
#   1. An entry point must never carry immutable headers, not even briefly.
#   2. This bulk upload would otherwise overwrite the LIVE catalogue with the
#      repo's copy before the merge step below has read it — so the merge read a
#      file it had itself just clobbered, concluded the site had no daily runs,
#      and erased a published run from latent-sky.dev.
upload_tree data   "$IMMUTABLE_CC" "web/catalogue.json" "web/*manifest.json"

say "── upload: root files"
for f in "$DIST"/*; do
  [[ -f "$f" ]] || continue
  name="${f##*/}"
  [[ "$name" == "index.html" ]] && continue
  ext="$(printf '%s' "${name##*.}" | tr '[:upper:]' '[:lower:]')"
  ctype=$(content_type_for "$ext")
  aws_mutate s3 cp "$f" "s3://$SITE_BUCKET/$name" --region "$REGION" \
    --content-type "$ctype" --cache-control "$ROOT_CC" --only-show-errors
done

# The entry points are index.html, the catalogue, and EVERY manifest the catalogue
# indexes. All are re-read on load and all change when the pipeline re-runs, so none
# may keep the immutable /data/* caching applied above — a catalogue frozen for a
# year would hide every event added after this deploy.
mapfile -t ENTRY_RELS < <(python - "$DIST/data/web" <<'PYENTRY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
rels = []
if (root / "catalogue.json").is_file():
    rels.append("catalogue.json")
rels.append("manifest.json")
cat = root / "catalogue.json"
if cat.is_file():
    for event in json.loads(cat.read_text(encoding="utf-8")).get("events", []):
        if event["id"].startswith("daily-"):
            continue  # lives only in the bucket, already no-cache; not re-uploaded here
        if event["manifest"] not in rels:
            rels.append(event["manifest"])
for rel in rels:
    print(rel)
PYENTRY
)
# Python on Windows writes CRLF, so every element would carry a trailing \r and
# fail the -f test below (caught by that test on the first dry-run, 27 Aug 2026).
# The pattern MUST come from a variable: $'\r' is ANSI-C quoting, which bash does
# not perform inside the double quotes of "${arr[@]%...}" — written inline it
# strips the literal characters $'\r' and silently changes nothing.
CR=$'\r'
ENTRY_RELS=("${ENTRY_RELS[@]%$CR}")

# ── merge the live daily runs into the catalogue, as late as possible ─────────
# infra/daily publishes daily-* events straight into the bucket; the repo never
# holds them. A deploy that uploaded only the repo catalogue would erase every
# live run from the site. This runs immediately before the upload so the window
# in which a concurrent daily publish could be lost is seconds, not minutes.
#
# A FETCH FAILURE IS NOT "no daily runs". Only a definite 404 means the catalogue
# is genuinely absent (a first deploy); anything else — expired credentials, a
# network blip, a typo in the bucket name — must stop the deploy, because
# continuing would silently wipe the live runs off latent-sky.dev.
say "── merging live daily runs into the staged catalogue"
LIVE_CAT="$NATIVE_TMP_EARLY/latentsky-live-catalogue.json"
rm -f "$LIVE_CAT"
if HEAD_ERR=$(aws s3api head-object --bucket "$SITE_BUCKET" --key data/web/catalogue.json \
                --region "$REGION" 2>&1 >/dev/null); then
  aws s3api get-object --bucket "$SITE_BUCKET" --key data/web/catalogue.json \
    --region "$REGION" "$LIVE_CAT" >/dev/null \
    || { say "REFUSING: the live catalogue exists but could not be downloaded — deploying now would erase every daily run from the site"; exit 1; }
  python - "$DIST/data/web/catalogue.json" "$LIVE_CAT" <<'PYMERGE'
import json, pathlib, sys
repo_path, live_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
repo = json.loads(repo_path.read_text(encoding="utf-8"))
live = json.loads(live_path.read_text(encoding="utf-8"))
dailies = [e for e in live.get("events", []) if e["id"].startswith("daily-")]
if not dailies:
    print("catalogue: the live site has no daily runs; repo catalogue used as is")
    raise SystemExit(0)
dailies.sort(key=lambda e: e["id"], reverse=True)
curated = [dict(e, default=False) for e in repo["events"] if not e["id"].startswith("daily-")]
merged = [dict(e, default=False) for e in dailies] + curated
merged[0]["default"] = True
ids = [e["id"] for e in merged]
mans = [e["manifest"] for e in merged]
if len(set(ids)) != len(ids) or len(set(mans)) != len(mans):
    raise SystemExit(f"REFUSING: merged catalogue has duplicate ids or manifests: {ids}")
repo["events"] = merged
repo_path.write_text(json.dumps(repo, indent=2) + "\n", encoding="utf-8")
print(f"catalogue: carried {len(dailies)} live daily run(s) over: {[e['id'] for e in dailies]}")
PYMERGE
elif grep -qi "Not Found\|404" <<<"$HEAD_ERR"; then
  say "no catalogue on the site yet (first deploy) — nothing to merge"
else
  say "REFUSING: could not determine whether a live catalogue exists: $HEAD_ERR"
  exit 1
fi

say "── upload: no-cache entry points (last, so their metadata wins): index.html ${ENTRY_RELS[*]}"
aws_mutate s3 cp "$DIST/index.html" "s3://$SITE_BUCKET/index.html" --region "$REGION" \
  --content-type "text/html" --cache-control "$NOCACHE_CC" --only-show-errors
for rel in "${ENTRY_RELS[@]}"; do
  [[ -f "$DIST/data/web/$rel" ]] || { say "REFUSING: entry point $rel missing from the staged tree"; exit 1; }
  aws_mutate s3 cp "$DIST/data/web/$rel" "s3://$SITE_BUCKET/data/web/$rel" \
    --region "$REGION" \
    --content-type "application/json" --cache-control "$NOCACHE_CC" --only-show-errors
done

PAGE_RELS=()
if [[ -d "$DIST/verification" ]]; then
  for f in "$DIST"/verification/*.html; do
    [[ -f "$f" ]] || continue
    PAGE_RELS+=("verification/${f##*/}")
    aws_mutate s3 cp "$f" "s3://$SITE_BUCKET/verification/${f##*/}" --region "$REGION" \
      --content-type "text/html" --cache-control "$NOCACHE_CC" --only-show-errors
  done
  say "── upload: verification pages (no-cache): ${PAGE_RELS[*]:-none}"
fi

# ── 7. Invalidation — the entry points ONLY, never /* ──────────────────────────
INVAL_PATHS=("/index.html")
for rel in "${ENTRY_RELS[@]}"; do INVAL_PATHS+=("/data/web/$rel"); done
for rel in "${PAGE_RELS[@]}"; do INVAL_PATHS+=("/$rel"); done
say "── invalidation: ${INVAL_PATHS[*]}"
# MSYS_NO_PATHCONV: Git Bash rewrites leading-slash args into Windows paths for
# native exes — "/index.html" reached CloudFront as "C:/Program Files/Git/index.html"
# and the API refused it (verified failure, 23 Aug 2026).
INVALIDATION_ID=$(MSYS_NO_PATHCONV=1 aws_mutate cloudfront create-invalidation --distribution-id "$DIST_ID" \
  --paths "${INVAL_PATHS[@]}" \
  --query "Invalidation.Id" --output text)
if [[ -z "$INVALIDATION_ID" ]]; then
  (( DRY_RUN )) || { say "FATAL: create-invalidation returned no id"; exit 1; }
else
  say "invalidation $INVALIDATION_ID — waiting…"
  aws_mutate cloudfront wait invalidation-completed --distribution-id "$DIST_ID" --id "$INVALIDATION_ID"
fi

# ── 8. Verification ────────────────────────────────────────────────────────────
say "── verify: https://$DOMAIN"
VERIFY_FAILURES=0

hget() {  # $1 = header blob, $2 = header name -> value (empty if absent)
  printf '%s' "$1" | tr -d '\r' | grep -i "^$2:" | head -n1 | cut -d: -f2- | sed 's/^ *//' || true
}

verify_one() {  # label path want_type [want_cache_control_substring]
  local label=$1 path=$2 want_type=$3 want_cc=${4:-}
  local url="https://$DOMAIN$path"
  say "· curl -sS -o /dev/null -D - -H 'Accept-Encoding: br, gzip' $url"
  if (( DRY_RUN )); then
    say "  [dry-run] skipped"
    return 0
  fi
  local hdrs code ctype cc ok=1 why=""
  hdrs=$(curl -sS -o /dev/null -D - -H 'Accept-Encoding: br, gzip' "$url" || true)
  code=$(printf '%s' "$hdrs" | head -n1 | awk '{print $2}' || true)
  ctype=$(hget "$hdrs" content-type)
  cc=$(hget "$hdrs" cache-control)
  [[ "$code" == "200" ]] || { ok=0; why+=" status=$code"; }
  [[ "$ctype" == "$want_type"* ]] || { ok=0; why+=" content-type='$ctype' (want $want_type)"; }
  if [[ -n "$want_cc" && "$cc" != *"$want_cc"* ]]; then ok=0; why+=" cache-control='$cc' (want *$want_cc*)"; fi
  if (( ok )); then
    say "  PASS  $label — 200 $ctype${cc:+; cache-control: $cc}"
  else
    say "  FAIL  $label —$why"
    VERIFY_FAILURES=$((VERIFY_FAILURES + 1))
  fi
}

# One webp frame, taken from the staged manifest so the check tracks real content.
FRAME_REL=$(python -c "import json,sys; m=json.load(open(sys.argv[1])); print(sorted(m['layers'].items())[0][1]['frames'][0])" "$DIST/data/web/manifest.json")

verify_one "root (default root object)" "/" "text/html"
verify_one "index.html" "/index.html" "text/html" "no-cache"
verify_one "manifest.json" "/data/web/manifest.json" "application/json" "no-cache"
verify_one "webp frame ($FRAME_REL)" "/data/web/$FRAME_REL" "image/webp" "immutable"
for rel in "${PAGE_RELS[@]}"; do
  verify_one "verification page ($rel)" "/$rel" "text/html" "no-cache"
done

# Compression check (§9.2): second request, after the first has warmed the edge.
say "· content-encoding on /index.html (request twice; check the second)"
if (( DRY_RUN )); then
  say "  [dry-run] skipped"
else
  curl -sS -o /dev/null -H 'Accept-Encoding: br, gzip' "https://$DOMAIN/index.html"
  ENC_HDRS=$(curl -sS -o /dev/null -D - -H 'Accept-Encoding: br, gzip' "https://$DOMAIN/index.html" || true)
  ENC=$(hget "$ENC_HDRS" content-encoding)
  if [[ "$ENC" == "br" || "$ENC" == "gzip" ]]; then
    say "  PASS  CloudFront compresses index.html (content-encoding: $ENC)"
  else
    say "  FAIL  no content-encoding on index.html — every visitor pays ~3x the bytes (§9.2)"
    VERIFY_FAILURES=$((VERIFY_FAILURES + 1))
  fi
fi

say ""
if (( DRY_RUN )); then
  say "DRY-RUN complete — no AWS mutation was performed. Site would be: https://$DOMAIN/"
elif (( VERIFY_FAILURES > 0 )); then
  say "DEPLOY FINISHED WITH $VERIFY_FAILURES FAILED CHECK(S) — https://$DOMAIN/"
  exit 1
else
  say "deploy verified: https://$DOMAIN/"
fi
