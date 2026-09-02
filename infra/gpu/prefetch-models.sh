#!/bin/bash
# Prefetch both model packages ONCE (laptop or any cheap machine) and stage them in
# S3, so no rented GPU minute is ever spent on an NGC download (§9.4). Both packages
# download unauthenticated. S3 at $0.023/GB-month (~$0.17/mo for 7.6 GB) beats every
# alternative, and the instance pulls them back over the free Gateway endpoint.
#
#   BUCKET=<bucket> ./prefetch-models.sh

set -euo pipefail
source "$(dirname "$0")/account_guard.sh"

BUCKET=${BUCKET:?set BUCKET=<your-s3-bucket>}
REGION=${REGION:-us-east-1}
CACHE=${EARTH2STUDIO_CACHE:-$HOME/.cache/earth2studio}

# load_model(), NOT load_default_package(). Constructing a Package only resolves
# the ngc:// / hf:// handle — Package.open() is what copies bytes, and load_model()
# is what calls it. This script used to call load_default_package() alone and then
# sync a COMPLETELY EMPTY cache to S3, reporting success either way. Identical bug
# to the one bake_models.py shipped on 26 Aug; found by audit 27 Aug 2026.
python - <<'PY'
import sys
from pathlib import Path

from earth2studio.models.auto import Package
from earth2studio.models.dx import CorrDiffTaiwan
from earth2studio.models.px import SFNO, StormCast

MIN_BYTES = {
    "sfno": 5 * 1024**3,
    "corrdiff_taiwan": 500 * 1024**2,
    "stormcast": 600 * 1024**2,
}


def cached_bytes(subdir: str) -> int:
    root = Path(Package.default_cache(subdir))
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


for subdir, cls in (("sfno", SFNO), ("corrdiff_taiwan", CorrDiffTaiwan),
                    ("stormcast", StormCast)):
    try:
        cls.load_model(cls.load_default_package())
    except Exception as exc:  # noqa: BLE001 — reported, then adjudicated on bytes
        print(f"WARNING {subdir}: load_model raised {type(exc).__name__}: {exc}",
              file=sys.stderr)
    got = cached_bytes(subdir)
    if got < MIN_BYTES[subdir]:
        raise SystemExit(
            f"FATAL {subdir}: only {got / 1024**3:.2f} GiB cached — nothing to stage"
        )
    print(f"{subdir}: {got / 1024**3:.2f} GiB cached", flush=True)
print("packages downloaded into local cache")
PY

aws s3 sync "$CACHE" "s3://$BUCKET/models/earth2studio/" --region "$REGION"
echo "staged to s3://$BUCKET/models/earth2studio/"
