#!/bin/bash
# The daily pod. Baked into the image (Dockerfile.daily) as the ENTRYPOINT, so a
# code change is an image rebuild; the launcher only passes DATA in env:
#
#   RUN_DATE   YYYY-MM-DD           INIT   ISO cycle, e.g. 2026-09-03T12:00:00
#   EVENT_ID   catalogue id         MEMBERS  ensemble size (1 = single run)
#   PUT_LOG    presigned PUT, the log, shipped every 60 s and on exit
#   PUT_STORES presigned PUT, tar of the zarr stores (scored tomorrow)
#   PUT_SITE   presigned PUT, tar of the encoded event tree
#   PUT_FINISHED presigned PUT, the exit marker — written WHATEVER happens
#
# and, when yesterday's run exists, the scoring pass:
#
#   PREV_DATE PREV_INIT PREV_EVENT_ID PREV_MEMBERS
#   GET_PREV_STORES  presigned GET of yesterday's stores tar
#   PUT_PREV_REPORT  presigned PUT, yesterday's verification page (html)
#   PUT_PREV_FSS     presigned PUT, yesterday's results (json)
#   PUT_PREV_SITE    presigned PUT, yesterday re-encoded with the observed radar
#
# Order matters and is load-bearing in three places:
#
#   1. Yesterday is scored FIRST. Its radar is complete and it costs no GPU, so a
#      GPU failure cannot take the verification with it.
#   2. Within the scoring stage the REPORT and the RESULTS go up BEFORE the site
#      tar. The site tar is what makes the publisher mark the day scored and link
#      the report from the live event, so a report that failed to upload must
#      never be linked.
#   3. Within the forecast stage the STORES go up before the site tar, and the
#      site tar goes up LAST of everything: its arrival terminates this pod.
#      A stores failure is logged and does NOT abandon the finished forecast —
#      it only costs tomorrow's verification, which is the cheaper loss.
#
# The exit trap always writes finished.json, so a pod that failed is stopped as
# promptly as one that succeeded. No AWS credential is ever in this container:
# every transfer is a presigned URL for one key.

# Hot-patch hook, same as the manual pods: the launcher hands in the current
# script (SCRIPT_B64) so a fix is a Lambda deploy rather than a 25-minute image
# rebuild. LATENTSKY_PATCHED stops the re-exec from looping.
if [ -n "${SCRIPT_B64:-}" ] && [ -z "${LATENTSKY_PATCHED:-}" ]; then
  if echo "$SCRIPT_B64" | base64 -d > /tmp/run.sh && bash -n /tmp/run.sh; then
    export LATENTSKY_PATCHED=1
    exec bash /tmp/run.sh
  fi
  echo "SCRIPT_B64 did not decode to a valid script; running the baked copy instead" >&2
fi

set -x
exec > /tmp/run.log 2>&1
echo "=== latent-sky daily run $(date -u +%FT%TZ) date=$RUN_DATE init=$INIT event=$EVENT_ID members=${MEMBERS:-1} ==="

cd /opt/latentsky
export PYTHONPATH=/opt/latentsky/pipeline/src
CFG=/opt/latentsky/pipeline/configs/event_daily_conus.yaml
TILES=/opt/latentsky/pipeline/assets/NaturalEarthII
MEMBERS=${MEMBERS:-1}
PREV_RC=0
TODAY_RC=0
STORES_RC=0

cat > /tmp/put.py <<'PYEOF'
import sys
import urllib.request

path, url = sys.argv[1], sys.argv[2]
with open(path, "rb") as fh:
    body = fh.read()
req = urllib.request.Request(url, data=body, method="PUT")
with urllib.request.urlopen(req, timeout=900) as resp:
    print(f"PUT {path} ({len(body)} bytes) -> HTTP {resp.status}", flush=True)
PYEOF
put() { python3 /tmp/put.py "$1" "$2"; }

( while true; do sleep 60; python3 /tmp/put.py /tmp/run.log "$PUT_LOG" >/dev/null 2>&1; done ) &
SHIPPER=$!

# The exit trap: ship the final log, then TELL THE WORLD WE ARE DONE. finished.json
# is what lets the publisher terminate a failed pod instead of leaving it billing
# until the reaper notices.
finish() {
  local status="ok"
  [ "$TODAY_RC" -eq 0 ] && [ "$PREV_RC" -eq 0 ] || status="failed"
  kill $SHIPPER 2>/dev/null
  python3 /tmp/put.py /tmp/run.log "$PUT_LOG" || true
  if [ -n "${PUT_FINISHED:-}" ]; then
    cat > /tmp/finished.json <<JSONEOF
{"date": "$RUN_DATE", "status": "$status", "forecast_rc": $TODAY_RC, "scoring_rc": $PREV_RC,
 "stores_rc": $STORES_RC, "members": $MEMBERS, "at": "$(date -u +%FT%TZ)"}
JSONEOF
    python3 /tmp/put.py /tmp/finished.json "$PUT_FINISHED" || true
  fi
}
trap finish EXIT

if [ -f /out/DAILY_DONE ]; then
  # The container restarted after finishing. Re-announce so the publisher stops
  # this pod, and get out — never idle, idling is billed.
  echo "DAILY_DONE present: this pod already finished. Re-announcing and exiting."
  exit 0
fi
for v in RUN_DATE INIT EVENT_ID PUT_LOG PUT_STORES PUT_SITE PUT_FINISHED; do
  if [ -z "${!v}" ]; then echo "FATAL: $v is not set"; TODAY_RC=78; exit 78; fi
done
mkdir -p /out
nvidia-smi | head -12

member_args() {  # $1 = stores dir, $2 = members -> --member flags for the encoder/scorer
  local dir=$1 n=$2 k args=""
  if [ "$n" -gt 1 ]; then
    for k in $(seq 0 $((n - 1))); do args="$args --member $dir/daily_m$(printf %02d $k)_hero.zarr"; done
  fi
  echo "$args"
}

store_list() {  # $1 = members -> the zarr stores the forecast writes, by name
  local n=$1 k out="daily.zarr"
  if [ "$n" -gt 1 ]; then
    for k in $(seq 0 $((n - 1))); do out="$out daily_m$(printf %02d $k)_hero.zarr"; done
  else
    out="$out daily_hero.zarr"
  fi
  echo "$out"
}

# ── 1. Score yesterday (CPU only) ──────────────────────────────────────────
if [ -n "${GET_PREV_STORES:-}" ]; then
  echo "=== SCORING $PREV_DATE (init $PREV_INIT, event $PREV_EVENT_ID) ==="
  (
    set -e
    rm -rf /prev && mkdir -p /prev/site
    python3 - "$GET_PREV_STORES" /prev/stores.tar.gz <<'PYEOF'
import sys, urllib.request
url, out = sys.argv[1], sys.argv[2]
with urllib.request.urlopen(url, timeout=900) as r, open(out, "wb") as fh:
    n = 0
    while True:
        chunk = r.read(1 << 20)
        if not chunk: break
        fh.write(chunk); n += len(chunk)
print(f"GET stores -> {n} bytes")
PYEOF
    tar xzf /prev/stores.tar.gz -C /prev
    ls /prev
    PM=${PREV_MEMBERS:-1}
    # For an ensemble the deterministic store IS member 0 — the same bytes, not a
    # separate execution. Say so explicitly (--hero-is-member) so verify_fss drops
    # the reproducibility claim, which only means something for two real runs.
    HERO=/prev/daily_hero.zarr
    SAME_AS_M00=""
    if [ "$PM" -gt 1 ]; then
      HERO=/prev/daily_m00_hero.zarr
      SAME_AS_M00="--hero-is-member 0"
      ln -sfn /prev/daily_m00_hero.zarr /prev/daily_hero.zarr   # encode_stormcast needs the sibling
    fi
    uv run python pipeline/tools/fetch_mrms.py --event-config "$CFG" --init "$PREV_INIT" --out /prev/mrms.npz
    uv run python pipeline/tools/verify_fss.py --hero-zarr "$HERO" $(member_args /prev "$PM") $SAME_AS_M00 \
      --mrms /prev/mrms.npz --out /prev/fss.json --event-id "$PREV_EVENT_ID" \
      --live-url "https://latent-sky.dev/?event=$PREV_EVENT_ID"
    uv run python pipeline/tools/fss_report.py --results /prev/fss.json --out /prev/report-fragment.html \
      --site-out "/prev/daily-$PREV_DATE.html"
    uv run python -m latentsky.encode_stormcast --zarr /prev/daily.zarr --event-config "$CFG" \
      --init "$PREV_INIT" --event-id "$PREV_EVENT_ID" --mrms /prev/mrms.npz $(member_args /prev "$PM") \
      --tiles "$TILES" --report-url "/verification/daily-$PREV_DATE.html" --out "/prev/site/$PREV_EVENT_ID"
    tar czf /prev/site.tar.gz -C /prev/site "$PREV_EVENT_ID"
    # Report and results FIRST: the site tar is what links the report publicly.
    put "/prev/daily-$PREV_DATE.html" "$PUT_PREV_REPORT"
    put /prev/fss.json "$PUT_PREV_FSS"
    put /prev/site.tar.gz "$PUT_PREV_SITE"
  )
  PREV_RC=$?
  echo "scoring exit: $PREV_RC"
else
  echo "no previous run to score"
fi

# ── 2. Forecast today ──────────────────────────────────────────────────────
echo "=== FORECAST $RUN_DATE init $INIT ($MEMBERS member(s)) ==="
(
  set -e
  rm -rf /out/daily*.zarr /out/site && mkdir -p /out/site
  uv run python pipeline/src/latentsky/forecast_stormcast.py --config "$CFG" --init "$INIT" \
    --output /out/daily.zarr --members "$MEMBERS"
  # encode_stormcast finds the hero store as <zarr-stem>_hero.zarr beside the
  # coarse one. An ensemble run writes daily_m00_hero.zarr and no such sibling,
  # so member 0 stands in. verify_fss resolves this link and therefore knows the
  # "single run" it scores IS a member, and makes no reproducibility claim.
  [ "$MEMBERS" -gt 1 ] && ln -sfn /out/daily_m00_hero.zarr /out/daily_hero.zarr
  uv run python -m latentsky.encode_stormcast --zarr /out/daily.zarr --event-config "$CFG" \
    --init "$INIT" --event-id "$EVENT_ID" $(member_args /out "$MEMBERS") --tiles "$TILES" \
    --out "/out/site/$EVENT_ID"
  tar czf /out/site.tar.gz -C /out/site "$EVENT_ID"
  # The store list is enumerated, not globbed: it names exactly what the forecast
  # wrote, so tar fails loudly if one is missing instead of quietly shipping a
  # short archive, and the daily_hero.zarr symlink is never archived.
  tar czf /out/stores.tar.gz -C /out $(store_list "$MEMBERS")
)
TODAY_RC=$?
echo "forecast exit: $TODAY_RC"

if [ $TODAY_RC -eq 0 ]; then
  # Stores are for TOMORROW's verification. If this upload fails the forecast is
  # still good and still goes on the site; only tomorrow's scoring is lost, and
  # finished.json records it so the check can say so.
  put /out/stores.tar.gz "$PUT_STORES" || STORES_RC=$?
  [ "${STORES_RC:-0}" -eq 0 ] || echo "WARNING: stores upload failed (rc=$STORES_RC) — tomorrow cannot score this run"
  # LAST upload of the run: its arrival terminates this pod.
  put /out/site.tar.gz "$PUT_SITE"
  TODAY_RC=$?
  echo "site upload exit: $TODAY_RC"
fi

touch /out/DAILY_DONE
if [ $TODAY_RC -eq 0 ] && [ $PREV_RC -eq 0 ]; then
  echo "=== ALL DONE $(date -u +%FT%TZ) ==="
  exit 0
fi
echo "=== FINISHED WITH FAILURES (scoring=$PREV_RC forecast=$TODAY_RC stores=$STORES_RC) $(date -u +%FT%TZ) ==="
exit 1
