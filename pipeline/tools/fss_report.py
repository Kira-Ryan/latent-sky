"""Render the verification results as a self-contained HTML page.

    python tools/fss_report.py --results data/verification/dixie_2025_pre.fss.json \
        --out data/verification/dixie_2025_pre.fss.html

The template (fss_report.html, beside this file) derives every figure it shows
in the browser from the embedded results JSON, so the prose cannot drift from
the numbers.
"""

from __future__ import annotations

import argparse
import json
import pathlib

TEMPLATE = pathlib.Path(__file__).with_name("fss_report.html")
PLACEHOLDER = "__RESULTS__"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    args = ap.parse_args(argv)

    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise SystemExit(f"{TEMPLATE} must contain exactly one {PLACEHOLDER}")
    data = json.dumps(json.loads(args.results.read_text(encoding="utf-8")), separators=(",", ":"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(template.replace(PLACEHOLDER, data), encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
