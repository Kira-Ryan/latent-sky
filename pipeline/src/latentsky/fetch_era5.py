"""Fetch real ERA5 analysis fields anonymously from ARCO and cache them — §3.6, §5.3.

Access pattern copied from probes/probe4-payload/probe4.py, which proved it live:
`xarray.open_zarr` over the public Google Cloud bucket with anonymous credentials
(gcsfs `token: "anon"`) — no account, no key, no queue, and never CDS.

§3.6 discipline: requested timestamps must be at least three months old, so the
fetch can only ever read FINAL ERA5 — ERA5T preliminary data for the most recent
two to three months is overwritten and byte-unstable. The 2021 dev timestamps are
final ERA5. The raw fetch is cached as an npz so re-encodes never re-download.

Variables (§8 rows 10-12 + the tcwv row): 2m_temperature,
10m_u_component_of_wind, 10m_v_component_of_wind, total_column_water_vapour.

Usage:
    python -m latentsky.fetch_era5 --times 2021-02-02T00:00:00Z ... --out data/dev/raw/era5_global.npz
    python -m latentsky.fetch_era5 --meta data/dev/raw/dev_meta.json --out data/dev/raw/era5_global.npz
"""

from __future__ import annotations

import argparse
import json
import pathlib
import time
from datetime import datetime, timedelta, timezone

import numpy as np

# The store probe4 proved anonymous access against (§3.6: ARCO, never CDS).
ARCO_STORE = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"

# npz key -> ARCO variable name
VARIABLES = {
    "t2m": "2m_temperature",
    "u10m": "10m_u_component_of_wind",
    "v10m": "10m_v_component_of_wind",
    "tcwv": "total_column_water_vapour",
}

# §3.6: ERA5T for the most recent ~3 months is later overwritten by final ERA5.
MIN_AGE_DAYS = 92

ERA5_SHAPE = (721, 1440)


class FetchError(RuntimeError):
    """The ERA5 fetch cannot proceed. Nothing is written on failure."""


def parse_utc(stamp: str) -> datetime:
    """ISO 8601 'YYYY-MM-DDTHH:MM:SSZ' (or without Z) -> aware UTC datetime."""
    s = stamp[:-1] if stamp.endswith("Z") else stamp
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise FetchError(f"unparseable timestamp {stamp!r}: {exc}") from exc
    if dt.tzinfo is not None:
        raise FetchError(f"timestamp {stamp!r} carries an explicit offset — use Z / naive UTC")
    return dt.replace(tzinfo=timezone.utc)


def check_final_era5(times: list[str]) -> None:
    """§3.6 gate: every requested time must be old enough to be FINAL ERA5."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=MIN_AGE_DAYS)
    young = [t for t in times if parse_utc(t) > cutoff]
    if young:
        raise FetchError(
            f"timestamps younger than {MIN_AGE_DAYS} days may still be ERA5T (preliminary, "
            f"later overwritten by final ERA5 — §3.6). Refusing: {young}"
        )


def cache_matches(out_path: pathlib.Path, times: list[str]) -> bool:
    """True if the npz cache exists and holds exactly the requested times, in order."""
    if not out_path.is_file():
        return False
    with np.load(out_path) as z:
        if sorted(z.files) != sorted([*VARIABLES, "latitude", "longitude", "times", "source"]):
            return False
        return [str(t) for t in z["times"]] == list(times)


def fetch(times: list[str], out_path: pathlib.Path, force: bool = False) -> pathlib.Path:
    """Fetch the four variables at `times` and write the npz cache. Idempotent."""
    if not times:
        raise FetchError("no timestamps requested")
    if sorted(times) != times or len(set(times)) != len(times):
        raise FetchError(f"timestamps must be strictly increasing and unique, got {times}")
    check_final_era5(times)

    if not force and cache_matches(out_path, times):
        print(f"cache hit: {out_path} already holds {len(times)} frames — not re-downloading")
        return out_path

    import xarray as xr  # deferred: encode-side code paths never need it

    t0 = time.perf_counter()
    print(f"opening {ARCO_STORE} (anonymous — no credentials, no CDS) ...")
    ds = xr.open_zarr(ARCO_STORE, chunks=None, storage_options={"token": "anon"})

    missing = [v for v in VARIABLES.values() if v not in ds]
    if missing:
        raise FetchError(f"ARCO store lacks variables {missing} — the store layout changed")

    lat = ds["latitude"].values.astype(np.float64)
    lon = ds["longitude"].values.astype(np.float64)
    if lat.shape != (ERA5_SHAPE[0],) or lat[0] != 90.0 or lat[-1] != -90.0 or not np.all(np.diff(lat) < 0):
        raise FetchError(f"expected latitude descending 90..-90 with 721 rows, got {lat[0]}..{lat[-1]} x{lat.size}")
    if lon.shape != (ERA5_SHAPE[1],) or lon[0] != 0.0 or lon[-1] >= 360.0 or not np.all(np.diff(lon) > 0):
        raise FetchError(f"expected longitude ascending 0..360 with 1440 cols, got {lon[0]}..{lon[-1]} x{lon.size}")

    sel_times = [np.datetime64(parse_utc(t).replace(tzinfo=None)) for t in times]
    sel = ds[list(VARIABLES.values())].sel(time=sel_times)

    out: dict[str, np.ndarray] = {}
    for key, arco_name in VARIABLES.items():
        print(f"  reading {arco_name} at {len(times)} times ...")
        arr = sel[arco_name].values.astype(np.float32)
        if arr.shape != (len(times), *ERA5_SHAPE):
            raise FetchError(f"{arco_name}: expected {(len(times), *ERA5_SHAPE)}, got {arr.shape}")
        if not np.isfinite(arr).all():
            raise FetchError(f"{arco_name}: non-finite values in the ARCO read")
        out[key] = arr

    source = (
        f"ARCO {ARCO_STORE} (anonymous GCS) — final ERA5, not ERA5T: all timestamps "
        f">{MIN_AGE_DAYS} days old per Architecture.md §3.6. "
        "Contains modified Copernicus Climate Change Service information 2021."
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out_path,
        **out,
        latitude=lat,
        longitude=lon,
        times=np.array(times),
        source=np.array(source),
    )
    print(
        f"wrote {out_path} ({out_path.stat().st_size:,} B) in {time.perf_counter() - t0:.1f} s — "
        f"{len(times)} frames x {len(VARIABLES)} variables at {ERA5_SHAPE[0]}x{ERA5_SHAPE[1]}"
    )
    return out_path


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--times", nargs="+", help="UTC timestamps, e.g. 2021-02-02T00:00:00Z")
    group.add_argument("--meta", type=pathlib.Path,
                       help="read the 'times' array from a dev_meta.json instead")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--force", action="store_true", help="re-download even on a cache hit")
    args = ap.parse_args(argv)

    times = args.times
    if times is None:
        times = json.loads(args.meta.read_text(encoding="utf-8"))["times"]
    fetch(list(times), args.out, force=args.force)


if __name__ == "__main__":
    main()
