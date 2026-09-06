"""Fetch HRRR's own composite reflectivity forecast for the cycle a StormCast run
was conditioned on, one message per lead, by byte range.

    python tools/fetch_hrrr_refc.py --event-config configs/event_daily_conus.yaml \
        --init 2026-09-05T12:00:00 --out data/zarr/hrrr_2026-09-05.npz

The operational comparator (DOCS/Verification-Protocol.md, Comparators). HRRR
is the model StormCast starts from and the model a forecaster would otherwise
look at, so scoring it on the same grid, hours and thresholds answers the
question a score alone cannot: better than what?

NOAA's open archive keeps every HRRR forecast file (~160 MB each) with a
sidecar .idx listing the byte offset of every GRIB message. REFC ("entire
atmosphere", composite reflectivity) is fetched by HTTP Range from that offset
to the next, about 3 MB a lead, and decoded with eccodes. The Lambert grid's
own latitude and longitude arrays are stored with the values, so the scorer
places the field by nearest cell exactly as it places the StormCast field.

Nothing is interpolated in time: lead h of the 12Z cycle is valid at 12Z + h,
which is the same instant as StormCast's lead h and the MRMS frame it is
scored against.
"""

from __future__ import annotations

import argparse
import pathlib
import time
import urllib.request
from datetime import datetime, timedelta

import numpy as np
import yaml

BUCKET = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
VARIABLE, LEVEL = "REFC", "entire atmosphere"
# StormCast v1's window (31.14-45.36 N, 250.37-274.58 E) plus a margin, east longitudes.
DEFAULT_BOX = (31.0, 45.5, 250.0, 275.0)


def cycle_key(init: datetime, lead: int) -> str:
    return f"hrrr.{init:%Y%m%d}/conus/hrrr.t{init:%H}z.wrfsfcf{lead:02d}.grib2"


def parse_idx(text: str, variable: str = VARIABLE, level: str = LEVEL) -> tuple[int, int | None]:
    """Byte range [start, end] of the message for `variable` at `level`.

    An .idx line reads `n:offset:d=YYYYMMDDHH:VAR:LEVEL:FCST:`. The message ends
    where the next one starts; the last message runs to the end of the file,
    which is reported as end=None.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for i, ln in enumerate(lines):
        parts = ln.split(":")
        if len(parts) >= 5 and parts[3] == variable and parts[4] == level:
            start = int(parts[1])
            end = int(lines[i + 1].split(":")[1]) - 1 if i + 1 < len(lines) else None
            return start, end
    raise LookupError(f"no {variable} at {level!r} in the index")


def fetch_message(key: str) -> tuple[bytes, tuple[int, int | None]]:
    idx = urllib.request.urlopen(f"{BUCKET}/{key}.idx", timeout=120).read().decode("utf-8", "replace")
    start, end = parse_idx(idx)
    req = urllib.request.Request(f"{BUCKET}/{key}", headers={"Range": f"bytes={start}-{'' if end is None else end}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        if r.status != 206:
            raise SystemExit(f"{key}: expected a 206 partial response, got {r.status}")
        return r.read(), (start, end)


def decode(message: bytes):
    """(values [Nj, Ni] float32 with missing as NaN, lat2d, lon2d east-positive)."""
    import eccodes  # deferred: the rest of the pipeline must not need it

    h = eccodes.codes_new_from_message(message)
    try:
        ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        short = eccodes.codes_get(h, "shortName")
        if short.lower() != "refc":
            raise SystemExit(f"decoded {short!r}, not refc — the index parse picked the wrong message")
        eccodes.codes_set(h, "missingValue", 9999.0)
        vals = eccodes.codes_get_values(h).reshape(nj, ni).astype(np.float32)
        lat = eccodes.codes_get_array(h, "latitudes").reshape(nj, ni).astype(np.float32)
        lon = eccodes.codes_get_array(h, "longitudes").reshape(nj, ni).astype(np.float32)
    finally:
        eccodes.codes_release(h)
    vals[vals >= 9000] = np.nan
    return vals, lat, lon % 360.0


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event-config", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--init", default=None, help="override the config's init (ISO) — the daily run")
    ap.add_argument("--box", type=float, nargs=4, metavar=("S", "N", "W", "E"), default=DEFAULT_BOX)
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(args.event_config.read_text(encoding="utf-8"))
    init = datetime.fromisoformat(args.init if args.init is not None else cfg["init"])
    nsteps = int(cfg["nsteps"])
    s, n, w, e = args.box

    frames, keys, ranges, t0 = [], [], [], time.time()
    lat = lon = rows = cols = None
    for lead in range(nsteps + 1):
        key = cycle_key(init, lead)
        message, rng = fetch_message(key)
        vals, la, lo = decode(message)
        if lat is None:
            inside = (la >= s) & (la <= n) & (lo >= w) & (lo <= e)
            r_idx, c_idx = np.nonzero(inside)
            rows, cols = slice(r_idx.min(), r_idx.max() + 1), slice(c_idx.min(), c_idx.max() + 1)
            lat, lon = la[rows, cols], lo[rows, cols]
        elif la.shape != vals.shape:
            raise SystemExit(f"{key}: grid changed mid-cycle")
        crop = vals[rows, cols]
        q = np.round(np.nan_to_num(crop, nan=-999.0) * 2.0)
        frames.append(q.astype(np.int16))
        keys.append(key)
        ranges.append(rng)
        print(f"  +{lead:2d}h {key}  bytes {rng[0]}-{rng[1]}  cells>=40dBZ={(crop >= 40).sum():>7,}  ({time.time() - t0:4.0f}s)", flush=True)

    valid = [(init + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ") for h in range(nsteps + 1)]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.out,
        refc_half_dbz=np.stack(frames),      # -1998 (i.e. -999 dBZ) marks a missing value
        lat=lat, lon=lon,
        valid=np.array(valid),
        keys=np.array(keys),
        byte_ranges=np.array([[a, -1 if b is None else b] for a, b in ranges]),
        cycle=np.array(init.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    print(f"wrote {args.out}: {np.stack(frames).shape} int16 on the HRRR grid crop, {args.out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
