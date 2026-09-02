"""Fetch every model package into EARTH2STUDIO_CACHE at image-build time.

Baking the weights into an image layer means a rented GPU never spends paid
minutes on NGC. Measured 26 Aug 2026: fetching them at run time into a RunPod
pod ran at 67.6 kB/s — a 27-hour ETA on the 6.87 GiB SFNO package — while the
ECR pull of an image layer runs at ~7 MB/s. Pay the download once, on AWS.

Why load_model() and not load_default_package(): constructing a Package only
resolves the ngc:// handle. Package.open()/resolve() are what copy bytes to
disk, and load_model() is what calls them (verified against the pinned 0.17.0
source). The first version of this script called load_default_package() alone,
printed "baked", and shipped an image containing no weights at all — the build
looked green and cost 90 minutes. Hence the byte assert below: this script may
only report success against bytes actually on disk.

SFNO and CorrDiff load on CPU by default. StormCast's load_model takes a
conditioning_data_source defaulting to GFS_FX(); constructing it touches no
network, and the weights download the same way, so it bakes here too.

Re-running against a base image that already has some packages is cheap: a
cached package downloads nothing and still passes its byte check.
"""

import sys
from pathlib import Path

from earth2studio.models.auto import Package
from earth2studio.models.dx import CorrDiffTaiwan
from earth2studio.models.px import SFNO, StormCast

# Tripwires, not checksums: set well under true size (SFNO 6.87 GiB, CorrDiff
# 684 MiB zipped) purely to catch "downloaded nothing". Keys are the cache
# subdirectories each model declares in its load_default_package cache_options.
MIN_BYTES = {
    "sfno": 5 * 1024**3,
    "corrdiff_taiwan": 500 * 1024**2,
    "stormcast": 600 * 1024**2,   # measured 0.746 GiB
}


def cached_bytes(subdir: str) -> int:
    """Total bytes on disk in a model's cache subdirectory."""
    root = Path(Package.default_cache(subdir))
    if not root.exists():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def bake(subdir: str, model_cls) -> None:
    try:
        model_cls.load_model(model_cls.load_default_package())
    except Exception as exc:  # noqa: BLE001 — reported, then adjudicated on bytes
        # Package.open() downloads before the model is constructed, so weights can
        # be fully cached even when instantiation trips on this GPU-less builder.
        # Bytes on disk are the success criterion; the failure is still shown.
        print(
            f"WARNING {subdir}: load_model raised {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

    got = cached_bytes(subdir)
    if got < MIN_BYTES[subdir]:
        raise SystemExit(
            f"FATAL {subdir}: only {got / 1024**3:.2f} GiB cached, "
            f"expected >= {MIN_BYTES[subdir] / 1024**3:.2f} GiB — model NOT baked"
        )
    print(f"{subdir}: {got / 1024**3:.2f} GiB cached", flush=True)


bake("sfno", SFNO)
bake("corrdiff_taiwan", CorrDiffTaiwan)
bake("stormcast", StormCast)
print("model packages baked into image cache")
