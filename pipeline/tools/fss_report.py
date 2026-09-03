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

# The template is a body fragment (it starts at <title>), which is what a hosted
# artifact wants. A page served from the site needs the document around it.
STANDALONE_HEAD = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
)
STANDALONE_SPLIT = "</style>\n"


def standalone(fragment: str) -> str:
    """Wrap the fragment in a full document: title+style into <head>, the rest into <body>."""
    if fragment.count(STANDALONE_SPLIT) < 1:
        raise SystemExit("template has no </style> to split head from body on")
    head, body = fragment.split(STANDALONE_SPLIT, 1)
    return STANDALONE_HEAD + head + STANDALONE_SPLIT + "</head>\n<body>\n" + body + "</body>\n</html>\n"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True, help="the body fragment")
    ap.add_argument("--site-out", type=pathlib.Path, default=None,
                    help="also write a standalone document for the site (data/verification/pages/...)")
    args = ap.parse_args(argv)

    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count(PLACEHOLDER) != 1:
        raise SystemExit(f"{TEMPLATE} must contain exactly one {PLACEHOLDER}")
    data = json.dumps(json.loads(args.results.read_text(encoding="utf-8")), separators=(",", ":"))
    page = template.replace(PLACEHOLDER, data)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes)")
    if args.site_out is not None:
        args.site_out.parent.mkdir(parents=True, exist_ok=True)
        args.site_out.write_text(standalone(page), encoding="utf-8")
        print(f"wrote {args.site_out} ({args.site_out.stat().st_size:,} bytes, standalone)")


if __name__ == "__main__":
    main()
