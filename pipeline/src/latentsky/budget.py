"""Payload budget gate — §8. Sums a directory, prints a table, exits 1 over the ceiling.

The 12 MB hard ceiling is the CI gate from §8 ("du -sb dist fails the build above
this"). Megabytes here are decimal (1 MB = 1,000,000 bytes), matching `du -sb`
arithmetic and every figure in the §8 table.

CLI:
    python -m latentsky.budget <directory> [--ceiling-mb 12.0]
"""

from __future__ import annotations

import argparse
import pathlib
import sys

DEFAULT_CEILING_MB = 12.0
MB = 1_000_000


def walk_sizes(root: pathlib.Path) -> dict[str, int]:
    """Bytes per immediate child of root (files and directories), recursively summed."""
    if not root.is_dir():
        raise NotADirectoryError(f"{root} is not a directory")
    sizes: dict[str, int] = {}
    for child in sorted(root.iterdir()):
        if child.is_file():
            sizes[child.name] = child.stat().st_size
        else:
            sizes[child.name + "/"] = sum(
                p.stat().st_size for p in child.rglob("*") if p.is_file()
            )
    return sizes


def report(root: pathlib.Path, ceiling_mb: float = DEFAULT_CEILING_MB) -> tuple[int, bool]:
    """Print the table. Returns (total_bytes, within_budget)."""
    sizes = walk_sizes(root)
    total = sum(sizes.values())
    ceiling_bytes = int(ceiling_mb * MB)

    width = max([len(name) for name in sizes] + [len("TOTAL")]) + 2
    print(f"\nPayload budget — {root}")
    print("-" * (width + 24))
    for name, size in sizes.items():
        print(f"  {name:<{width}} {size:>12,} B  {size / MB:7.3f} MB")
    print("-" * (width + 24))
    print(f"  {'TOTAL':<{width}} {total:>12,} B  {total / MB:7.3f} MB")
    print(f"  {'CEILING':<{width}} {ceiling_bytes:>12,} B  {ceiling_mb:7.3f} MB")

    within = total <= ceiling_bytes
    if within:
        headroom = ceiling_bytes - total
        print(f"  WITHIN BUDGET — {headroom / MB:.3f} MB of headroom\n")
    else:
        print(f"  OVER BUDGET by {(total - ceiling_bytes) / MB:.3f} MB — build must fail\n")
    return total, within


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("directory", type=pathlib.Path)
    ap.add_argument("--ceiling-mb", type=float, default=DEFAULT_CEILING_MB)
    args = ap.parse_args(argv)

    _, within = report(args.directory, args.ceiling_mb)
    return 0 if within else 1


if __name__ == "__main__":
    sys.exit(main())
