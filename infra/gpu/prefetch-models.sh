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

python - <<'PY'
from earth2studio.models.dx import CorrDiffTaiwan
from earth2studio.models.px import SFNO
SFNO.load_default_package()
CorrDiffTaiwan.load_default_package()
print("packages resolved into local cache")
PY

aws s3 sync "$CACHE" "s3://$BUCKET/models/earth2studio/" --region "$REGION"
echo "staged to s3://$BUCKET/models/earth2studio/"
