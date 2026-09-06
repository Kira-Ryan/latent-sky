"""Recompute a results file's headline under the current rule, and carry it into
the manifest that states it.

    python tools/rescore_headline.py --results data/verification/dixie_2025_pre.fss.json \\
        --manifest data/web/dixie/manifest.json

The per-hour scores are never touched: the headline is a summary of numbers that
already exist, so a rule change means re-summarising, not re-scoring. The
manifest is only updated if it is marked scored and its verificationSummary is
present; the new summary replaces the old one in place and nothing else in the
manifest changes. Prints the old and new headline so the change is visible.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from latentsky import verify  # noqa: E402


def rescore(results_path: pathlib.Path, manifest_path: pathlib.Path | None) -> dict:
    results = json.loads(results_path.read_text(encoding="utf-8"))
    old = results.get("headline")
    new = verify.headline(results)
    results["headline"] = new
    verify.comparators(results)   # re-attaches the baselines' figures when the file has them
    results_path.write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    print(f"{results_path}:\n  old {json.dumps(old)}\n  new {json.dumps(new)}")
    if manifest_path is not None:
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        run = m["run"]
        if run.get("verification") != "scored" or "verificationSummary" not in run:
            raise SystemExit(f"{manifest_path}: not a scored manifest with a summary; refusing to add one")
        if run.get("init") != results.get("init"):
            raise SystemExit(f"{manifest_path}: init {run.get('init')} is not the results' {results.get('init')}")
        run["verificationSummary"] = new
        manifest_path.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
        print(f"  -> {manifest_path} verificationSummary replaced")
    return new


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=pathlib.Path, required=True)
    ap.add_argument("--manifest", type=pathlib.Path, default=None)
    args = ap.parse_args(argv)
    rescore(args.results, args.manifest)


if __name__ == "__main__":
    main()
