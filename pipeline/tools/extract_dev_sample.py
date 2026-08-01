"""Extract a local development dataset from NVIDIA's CorrDiff inference package.

Pulls the five real sample timesteps (2021, incl. Typhoon Chanthu on 2021-09-12) into
plain .npz arrays under data/dev/raw/, on the model's true 448x448 output grid, so the
encode pipeline and the web app can be developed against real coarse/fine fields before
any GPU run exists.

LICENCE: the source dataset is CC BY-NC-ND 4.0. data/dev/ is gitignored and must stay
local-only. Nothing derived from it may be committed, deployed, or published. The shipped
site uses exclusively our own model output.

Usage:
    python pipeline/tools/extract_dev_sample.py --package <dir containing the unpacked zarr>
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib

import numpy as np
import zarr

ZARR_NAME = "2023-01-24-cwb-4years_5times.zarr"

# Channel indices shared by the cwb (fine/target) and era5 (coarse/input, resampled onto
# the fine grid) arrays. Verified against cwb_variable / era5_variable in Probe 2.
CH = {"mrr": 0, "t2m": 17, "u10": 18, "v10": 19}


def crop(a: np.ndarray) -> np.ndarray:
    """The model's output grid is XLAT/XLONG cropped by one cell all round (448x448)."""
    return a[..., 1:-1, 1:-1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", type=pathlib.Path, required=True,
                    help="directory that contains the unpacked dataset/ folder")
    ap.add_argument("--out", type=pathlib.Path,
                    default=pathlib.Path("data/dev/raw"))
    args = ap.parse_args()

    store = next(args.package.rglob(ZARR_NAME), None)
    if store is None:
        raise SystemExit(f"{ZARR_NAME} not found under {args.package}")
    g = zarr.open(str(store), mode="r")

    args.out.mkdir(parents=True, exist_ok=True)

    lat = crop(np.asarray(g["XLAT"][:], dtype=np.float32))
    lon = crop(np.asarray(g["XLONG"][:], dtype=np.float32))

    base = dt.datetime(2018, 1, 1)
    times = [
        (base + dt.timedelta(hours=int(h))).strftime("%Y-%m-%dT%H:%M:%SZ")
        for h in np.asarray(g["time"][:])
    ]

    fields: dict[str, np.ndarray] = {"lat": lat, "lon": lon}
    for side, arr_name in (("fine", "cwb"), ("coarse", "era5")):
        arr = g[arr_name]
        u = crop(np.asarray(arr[:, CH["u10"]], dtype=np.float32))
        v = crop(np.asarray(arr[:, CH["v10"]], dtype=np.float32))
        fields[f"{side}_wind10m"] = np.hypot(u, v)
        fields[f"{side}_t2m"] = crop(np.asarray(arr[:, CH["t2m"]], dtype=np.float32))
        if side == "fine":  # the coarse model has no reflectivity — that is the point
            fields["fine_mrr"] = crop(np.asarray(arr[:, CH["mrr"]], dtype=np.float32))

    np.savez_compressed(args.out / "cwb_sample.npz", **fields)

    meta = {
        "source": "corrdiff_inference_package@1 dataset (CC BY-NC-ND 4.0 — LOCAL DEV ONLY)",
        "grid": [int(lat.shape[0]), int(lat.shape[1])],
        "times": times,
        "note": ("era5 fields are the coarse input resampled onto the fine grid by the "
                 "package authors; the true native input grid is 36x40 at 0.25 deg."),
        "ranges": {
            k: [float(np.nanmin(v)), float(np.nanmax(v))]
            for k, v in fields.items() if k not in ("lat", "lon")
        },
    }
    (args.out / "dev_meta.json").write_text(json.dumps(meta, indent=2))

    for k, v in fields.items():
        print(f"  {k:64s} {str(v.shape):18s} {v.dtype}")
    print(f"\nwrote {args.out / 'cwb_sample.npz'} "
          f"({(args.out / 'cwb_sample.npz').stat().st_size / 1e6:.1f} MB) + dev_meta.json")
    print("times:", ", ".join(times))


if __name__ == "__main__":
    main()
