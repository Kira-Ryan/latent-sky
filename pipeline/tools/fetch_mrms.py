"""Fetch MRMS composite reflectivity for every valid hour of a StormCast run.

    python tools/fetch_mrms.py --event-config configs/event_dixie_2025_preconvective.yaml \
        --out data/zarr/mrms_dixie_2025_pre.npz

Lists NOAA's open bucket (2-minute cadence, anonymous), picks the file nearest
each top of the hour from init through init + nsteps, decodes the GRIB2 with
eccodes, crops to the StormCast domain with a margin, and stores int16 half-dBZ.
Sentinels (-999 no coverage, -99 no echo) are kept verbatim; resolving them is
the scorer's job (latentsky.verify).

eccodes installs from a plain wheel on Windows (`pip install eccodes`), so this
runs on the laptop. About 2 MB per hour downloaded; a 19-hour run takes a minute.
"""

from __future__ import annotations

import argparse
import gzip
import pathlib
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import numpy as np
import yaml

BUCKET = "https://noaa-mrms-pds.s3.amazonaws.com"
PRODUCT = "CONUS/MergedReflectivityQCComposite_00.50"

# MRMS CONUS grid: 3500 x 7000 at 0.01 deg, row 0 north, lon 0..360 east.
MRMS_LAT = 54.995 - 0.01 * np.arange(3500)
MRMS_LON = 230.005 + 0.01 * np.arange(7000)

# StormCast v1's window (31.14-45.36 N, 250.37-274.58 E) plus a margin.
DEFAULT_BOX = (31.0, 45.5, 250.0, 275.0)  # south, north, west, east (east longitudes)


def list_day(day: str) -> list[str]:
    keys, token = [], None
    while True:
        url = f"{BUCKET}/?list-type=2&prefix={PRODUCT}/{day}/&max-keys=1000"
        if token:
            url += "&continuation-token=" + urllib.parse.quote(token)
        root = ET.fromstring(urllib.request.urlopen(url, timeout=60).read())
        ns = {"s3": root.tag.split("}")[0].strip("{")}
        keys += [k.text for k in root.findall(".//s3:Key", ns)]
        nxt = root.find("s3:NextContinuationToken", ns)
        if nxt is None:
            return keys
        token = nxt.text


def plan_frames(init: datetime, nsteps: int) -> list[dict]:
    days = sorted({(init + timedelta(hours=h)).strftime("%Y%m%d") for h in range(nsteps + 1)})
    stamps = {}
    for day in days:
        for key in list_day(day):
            ts = key.rsplit("_", 1)[1].replace(".grib2.gz", "")
            stamps[datetime.strptime(ts, "%Y%m%d-%H%M%S")] = key
    if not stamps:
        raise SystemExit(f"no MRMS files listed for {days}")
    plan = []
    for h in range(nsteps + 1):
        valid = init + timedelta(hours=h)
        best = min(stamps, key=lambda t: abs((t - valid).total_seconds()))
        plan.append({"lead": h, "valid": valid.strftime("%Y-%m-%dT%H:%M:%SZ"), "key": stamps[best],
                     "offset_s": (best - valid).total_seconds()})
    return plan


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event-config", type=pathlib.Path, required=True)
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--box", type=float, nargs=4, metavar=("S", "N", "W", "E"), default=DEFAULT_BOX,
                    help="crop box, degrees, east longitudes (default: StormCast v1 window + margin)")
    args = ap.parse_args(argv)

    import eccodes  # deferred: the rest of the pipeline must not need it

    cfg = yaml.safe_load(args.event_config.read_text(encoding="utf-8"))
    init = datetime.fromisoformat(cfg["init"])
    nsteps = int(cfg["nsteps"])
    plan = plan_frames(init, nsteps)
    worst = max(abs(p["offset_s"]) for p in plan)
    print(f"{len(plan)} frames from {plan[0]['valid']} to {plan[-1]['valid']}, worst offset {worst:.0f}s")
    if worst > 300:
        raise SystemExit(f"nearest MRMS file is {worst:.0f}s from a valid time — refusing to score against it")

    s, n, w, e = args.box
    rows = np.where((MRMS_LAT >= s) & (MRMS_LAT <= n))[0]
    cols = np.where((MRMS_LON >= w) & (MRMS_LON <= e))[0]

    frames, t0 = [], time.time()
    for p in plan:
        raw = urllib.request.urlopen(f"{BUCKET}/{p['key']}", timeout=180).read()
        h = eccodes.codes_new_from_message(gzip.decompress(raw))
        ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        if (nj, ni) != (3500, 7000):
            raise SystemExit(f"unexpected MRMS grid {nj}x{ni} in {p['key']}")
        a = eccodes.codes_get_values(h).reshape(nj, ni)[np.ix_(rows, cols)]
        eccodes.codes_release(h)
        q = np.round(a * 2.0)
        if q.min() < -32768 or q.max() > 32767:
            raise SystemExit("half-dBZ does not fit int16")
        frames.append(q.astype(np.int16))
        print(f"  +{p['lead']:2d}h {p['valid']}  cells>=40dBZ={(a >= 40).sum():>7,}  ({time.time() - t0:4.0f}s)", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        args.out,
        refc_half_dbz=np.stack(frames),
        lat=MRMS_LAT[rows].astype(np.float32),
        lon=MRMS_LON[cols].astype(np.float32),
        valid=np.array([p["valid"] for p in plan]),
        offset_s=np.array([p["offset_s"] for p in plan]),
        keys=np.array([p["key"] for p in plan]),
    )
    print(f"wrote {args.out}: {np.stack(frames).shape} int16, {args.out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
