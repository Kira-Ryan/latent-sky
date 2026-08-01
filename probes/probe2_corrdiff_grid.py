"""Probe 2 — measure the CorrDiff Taiwan grid straight from NVIDIA's inference package.

Answers, with no GPU and no credentials:
  * is the output 2 km or 3 km?          -> 2.0684 km (measured, great-circle)
  * what shape is the output grid?       -> 448 x 448 (450 x 450 source, cropped [1:-1, 1:-1])
  * what is the true bounding box?       -> 19.5187-27.9282 N, 116.1372-125.5459 E
  * is it equirectangular?               -> NO. up to 37.3 km of longitude drift down a column.
  * what are the real value ranges?      -> see the ramp table in DOCS/Architecture.md 7.1
  * what are the licences?               -> checkpoints Apache-2.0, dataset CC BY-NC-ND 4.0

Usage:
    pip install zarr numpy
    python probes/probe2_corrdiff_grid.py --download   # 717 MB, unauthenticated
    python probes/probe2_corrdiff_grid.py              # if already unpacked alongside

Verified 31 July 2026 against corrdiff_inference_package@1
(sha256 0718f1e60e97fc16efa0928d3eecc70a86825f3704ddd699804ce45ffd391747).
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import urllib.request
import zipfile

import numpy as np
import zarr

NGC_URL = (
    "https://api.ngc.nvidia.com/v2/models/nvidia/modulus/"
    "corrdiff_inference_package/versions/1/zip"
)
EXPECTED_SHA256 = "0718f1e60e97fc16efa0928d3eecc70a86825f3704ddd699804ce45ffd391747"
ZARR_REL = (
    "corrdiff_inference_package/dataset/2023-01-24-cwb-4years_5times.zarr"
)
R_EARTH_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two arrays of points, in km."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlam = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2) ** 2
    return 2 * R_EARTH_KM * np.arcsin(np.sqrt(a))


def fetch(root: pathlib.Path) -> None:
    """Download and unpack the package. Nested zip, so unpack twice."""
    outer = root / "corrdiff_pkg.zip"
    if not outer.exists():
        print(f"downloading {NGC_URL}")
        urllib.request.urlretrieve(NGC_URL, outer)

    digest = hashlib.sha256()
    with outer.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    got = digest.hexdigest()
    print(f"sha256 {got}")
    if got != EXPECTED_SHA256:
        raise SystemExit(f"checksum mismatch — expected {EXPECTED_SHA256}")

    work = root / "corrdiff_pkg"
    work.mkdir(exist_ok=True)
    with zipfile.ZipFile(outer) as zf:
        zf.extractall(work)
    for nested in work.rglob("*.zip"):
        with zipfile.ZipFile(nested) as zf:
            zf.extractall(nested.parent)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="fetch the 717 MB package first")
    ap.add_argument("--root", default=".", type=pathlib.Path)
    args = ap.parse_args()

    if args.download:
        fetch(args.root)

    store = next(args.root.rglob(ZARR_REL.split("/")[-1]), None)
    if store is None:
        raise SystemExit("zarr store not found — run with --download")
    group = zarr.open(str(store), mode="r")

    # earth2studio registers out_lat/out_lon as XLAT/XLONG cropped by one cell all round.
    # corrdiff.py:1323  self.register_buffer("out_lat", out_lat[1:-1, 1:-1])
    lat = np.asarray(group["XLAT"][:], dtype=np.float64)[1:-1, 1:-1]
    lon = np.asarray(group["XLONG"][:], dtype=np.float64)[1:-1, 1:-1]
    ny, nx = lat.shape

    print(f"\noutput grid            {ny} x {nx}")
    print(f"latitude               {lat.min():.4f} -> {lat.max():.4f} N")
    print(f"longitude              {lon.min():.4f} -> {lon.max():.4f} E")

    dx = haversine_km(lat[:, :-1], lon[:, :-1], lat[:, 1:], lon[:, 1:])
    dy = haversine_km(lat[:-1, :], lon[:-1, :], lat[1:, :], lon[1:, :])
    native = 0.5 * (dx.mean() + dy.mean())
    print(f"cell size              {native:.4f} km  (x {dx.mean():.4f}, y {dy.mean():.4f})")

    # On a true equirectangular grid these are both zero.
    lat_drift = (lat.max(axis=1) - lat.min(axis=1)).max()
    lon_drift = (lon.max(axis=0) - lon.min(axis=0)).max()
    print(f"lat drift along a row  {lat_drift:.4f} deg ({lat_drift * 111.195:.1f} km)")
    print(
        f"lon drift down a col   {lon_drift:.4f} deg "
        f"({lon_drift * 111.195 * np.cos(np.radians(lat.mean())):.1f} km)"
    )
    print(f"equirectangular?       {'yes' if max(lat_drift, lon_drift) < 1e-4 else 'NO - regrid required'}")

    print("\nregrid targets covering the measured bbox:")
    for factor in (1.0, 1.25, 1.5, 2.0):
        step = native / factor
        dlat = step / 111.195
        dlon = step / (111.195 * np.cos(np.radians(lat.mean())))
        h = int(np.ceil((lat.max() - lat.min()) / dlat))
        w = int(np.ceil((lon.max() - lon.min()) / dlon))
        print(f"  {factor:4.2f}x  step {step:5.3f} km  ->  {w} x {h} px  ({w * h / (ny * nx):.2f}x native)")

    print("\nmeasured field ranges (5 sample steps, incl. 2021-09-12 / Typhoon Chanthu):")
    fine = np.hypot(group["cwb"][:, 18, :, :], group["cwb"][:, 19, :, :])
    coarse = np.hypot(group["era5"][:, 18, :, :], group["era5"][:, 19, :, :])
    mrr = np.asarray(group["cwb"][:, 0, :, :])
    for label, arr, unit in (
        ("fine 10m wind", fine, "m/s"),
        ("coarse 10m wind", coarse, "m/s"),
        ("reflectivity", mrr, "dBZ"),
    ):
        print(
            f"  {label:16} max {arr.max():6.2f} {unit}   "
            f"p99 {np.percentile(arr, 99):6.2f}   mean {arr.mean():5.2f}"
        )
    print(
        f"\n  downscaled peak wind is {fine.max() / coarse.max():.2f}x the coarse peak - "
        "the coarse model cannot represent it at all."
    )


if __name__ == "__main__":
    main()
