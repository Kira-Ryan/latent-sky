"""Licence audit — the gate behind .github/workflows/licences.yml (Architecture.md §12).

Deny-unknown heuristic: scan the pipeline's configs and source for
  1. asset URIs   — ngc://, hf://, gs://, s3://
  2. known asset/package names — earth2studio, sfno, corrdiff, cmcrameri, cmocean,
     cmweather, cesium, Natural Earth
and FAIL unless every hit is covered by an entry in licences/MANIFEST.yaml.
A URI hit matches an entry when either is a prefix of the other (so the manifest may
list a bucket while the code pins a path inside it). A name hit requires the named
manifest entry to exist.

This is a heuristic, not a proof: a brand-new dependency referenced without a scheme
URI and absent from KNOWN_NAMES is invisible to it. The scheme list and KNOWN_NAMES
are therefore the two lists to extend when the pipeline grows.

Also validates MANIFEST.yaml structure: every entry needs name, uri and checked_on,
plus a licence either at top level or on every item of `contains`.

CLI (run from anywhere; paths resolve relative to the repository root):
    python pipeline/tools/licence_audit.py [--manifest licences/MANIFEST.yaml]
Exit 0 on a clean audit, 1 on any unmatched reference or manifest defect.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# What gets scanned. Dockerfile and pyproject.toml are included deliberately: the
# Dockerfile pins earth2studio and the pyproject names every colormap package.
SCAN_GLOBS = (
    "pipeline/configs/**/*.yaml",
    "pipeline/src/latentsky/**/*.py",
    "pipeline/tools/*.py",
    "pipeline/Dockerfile",
    "pipeline/pyproject.toml",
)

URI_RE = re.compile(r"\b(?:ngc|hf|gs|s3)://[^\s\"'()\[\]<>,]+")

# name-token regex (case-insensitive) -> required MANIFEST.yaml entry name
KNOWN_NAMES: dict[str, str] = {
    r"earth2studio": "earth2studio",
    r"sfno": "sfno_73ch_small",
    r"corrdiff": "corrdiff_inference_package",
    r"cmcrameri": "scientific-colour-maps",
    r"cmocean": "cmocean",
    r"cmweather": "cmweather",
    r"cesium": "cesiumjs",
    r"natural[\s_-]?earth": "natural-earth",
}


class Hit:
    def __init__(self, path: pathlib.Path, line: int, kind: str, text: str):
        self.path, self.line, self.kind, self.text = path, line, kind, text

    def where(self) -> str:
        return f"{self.path.relative_to(REPO_ROOT)}:{self.line}"


def load_manifest(path: pathlib.Path) -> list[dict]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or not isinstance(doc.get("assets"), list):
        raise SystemExit(f"MANIFEST malformed: {path} must be a mapping with an 'assets' list")
    problems = []
    for i, entry in enumerate(doc["assets"]):
        label = entry.get("name", f"assets[{i}]")
        for key in ("name", "uri", "checked_on"):
            if key not in entry:
                problems.append(f"{label}: missing '{key}'")
        contains = entry.get("contains")
        if "licence" not in entry:
            if not contains:
                problems.append(f"{label}: no 'licence' and no 'contains' items carrying one")
            else:
                for item in contains:
                    if "licence" not in item:
                        problems.append(f"{label}: contains item {item.get('path')} missing 'licence'")
    if problems:
        print("MANIFEST structural defects:")
        for p in problems:
            print(f"  - {p}")
        raise SystemExit(1)
    return doc["assets"]


def scan() -> list[Hit]:
    hits: list[Hit] = []
    files: list[pathlib.Path] = []
    for pattern in SCAN_GLOBS:
        files.extend(sorted(REPO_ROOT.glob(pattern)))
    if not files:
        raise SystemExit("scan set is empty — SCAN_GLOBS no longer matches the repository layout")
    name_res = {re.compile(rx, re.IGNORECASE): entry for rx, entry in KNOWN_NAMES.items()}
    for path in files:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for m in URI_RE.finditer(line):
                hits.append(Hit(path, lineno, "uri", m.group(0)))
            for rx, entry in name_res.items():
                if rx.search(line):
                    hits.append(Hit(path, lineno, "name", entry))
    return hits


def norm_uri(uri: str) -> str:
    return uri.rstrip("/").lower()


def audit(manifest_path: pathlib.Path) -> int:
    assets = load_manifest(manifest_path)
    names = {a["name"] for a in assets}
    uris = {norm_uri(str(a["uri"])): a["name"] for a in assets}

    hits = scan()
    unmatched: list[Hit] = []
    matched: dict[str, set[str]] = {}

    for hit in hits:
        if hit.kind == "uri":
            h = norm_uri(hit.text)
            owner = next(
                (name for uri, name in uris.items() if h.startswith(uri) or uri.startswith(h)),
                None,
            )
        else:
            owner = hit.text if hit.text in names else None
        if owner is None:
            unmatched.append(hit)
        else:
            matched.setdefault(owner, set()).add(hit.where())

    print(f"Licence audit — {len(hits)} reference hits across the pipeline scan set")
    for owner in sorted(matched):
        locs = sorted(matched[owner])
        shown = ", ".join(locs[:3]) + (f", … +{len(locs) - 3}" if len(locs) > 3 else "")
        print(f"  covered  {owner:<30} {len(locs):>3} site(s)  {shown}")

    if unmatched:
        print(f"\nFAIL — {len(unmatched)} reference(s) with NO entry in {manifest_path.relative_to(REPO_ROOT)}:")
        for hit in unmatched:
            print(f"  {hit.where()}  [{hit.kind}]  {hit.text}")
        print("\nAdd a manifest entry (name, uri, licence, checked_on, redistributable) or remove the reference.")
        return 1

    print("\nPASS — every scanned asset reference is covered by the licence manifest.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--manifest",
        type=pathlib.Path,
        default=REPO_ROOT / "licences" / "MANIFEST.yaml",
    )
    args = ap.parse_args(argv)
    return audit(args.manifest.resolve())


if __name__ == "__main__":
    sys.exit(main())
