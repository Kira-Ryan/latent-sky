"""Neighbourhood verification of a hero forecast against observed radar.

Gridpoint scores punish a convection-allowing model twice for a storm that is
slightly misplaced: a miss where it was and a false alarm where it went. At 3 km a
storm 20 km off position scores worse by RMSE than forecasting no storm at all.
The standard for convection is therefore the Fractions Skill Score (Roberts and
Lean 2008): threshold both fields, take the fraction of exceeding cells in a
square neighbourhood around every point, and compare the two fraction fields.

Everything is scored on the SAME equirectangular grid the site renders, rebuilt
here with the encoder's own regrid rule and (optionally) asserted equal to the
shipped manifest, so what is scored is exactly what is shown.

MRMS sentinels, established empirically on 31 Aug 2026 (zero -999 cells inside
the central-US domain; the Gulf and Atlantic corners entirely -999):
    -999  outside radar coverage  -> excluded from every statistic
     -99  coverage, no echo       -> a real 0 dBZ
"""

from __future__ import annotations

import json
import math
import pathlib

import numpy as np
from scipy.ndimage import uniform_filter

from . import regrid

KM_PER_DEG = 111.32
MRMS_NO_COVERAGE = -999.0
MRMS_NO_ECHO = -99.0


def to_180(lon: np.ndarray) -> np.ndarray:
    """Wrap 0..360 east longitudes into -180..180."""
    return ((np.asarray(lon) + 180.0) % 360.0) - 180.0


# ------------------------------------------------------------------ the maths

def fss(f: np.ndarray, o: np.ndarray, valid: np.ndarray, thr: float, win: int) -> float:
    """Fractions Skill Score over the valid cells.

    Invalid cells are zero in BOTH binary fields before filtering, so the mask
    edge neither creates nor absorbs neighbourhood mass; the means run over
    valid cells only. `win` is the square neighbourhood width in pixels (odd).
    NaN when neither field exceeds the threshold anywhere.
    """
    bf = np.where(valid, f >= thr, False).astype(np.float64)
    bo = np.where(valid, o >= thr, False).astype(np.float64)
    if win > 1:
        pf = uniform_filter(bf, size=win, mode="constant", cval=0.0)
        po = uniform_filter(bo, size=win, mode="constant", cval=0.0)
    else:
        pf, po = bf, bo
    pf, po = pf[valid], po[valid]
    num = float(np.mean((pf - po) ** 2))
    den = float(np.mean(pf**2) + np.mean(po**2))
    return 1.0 - num / den if den > 0 else float("nan")


def fss_probabilistic(p: np.ndarray, o: np.ndarray, valid: np.ndarray, thr: float, win: int) -> float:
    """FSS with an ensemble PROBABILITY field in place of the forecast binary.

    `p` is the fraction of members at or above `thr` per cell (0..1). Same
    treatment of the mask as fss(); this is how an ensemble is scored on the same
    footing as a single member.
    """
    pf0 = np.where(valid, p, 0.0).astype(np.float64)
    bo = np.where(valid, o >= thr, False).astype(np.float64)
    if win > 1:
        pf = uniform_filter(pf0, size=win, mode="constant", cval=0.0)
        po = uniform_filter(bo, size=win, mode="constant", cval=0.0)
    else:
        pf, po = pf0, bo
    pf, po = pf[valid], po[valid]
    num = float(np.mean((pf - po) ** 2))
    den = float(np.mean(pf**2) + np.mean(po**2))
    return 1.0 - num / den if den > 0 else float("nan")


def coverage(x: np.ndarray, valid: np.ndarray, thr: float) -> float:
    """Fraction of valid cells at or above thr."""
    return float(np.mean(x[valid] >= thr))


def centroid(x: np.ndarray, valid: np.ndarray, lat_centres: np.ndarray, lon_centres: np.ndarray, thr: float):
    """Mean lat/lon of cells at or above thr; None if there are none."""
    mask = valid & (x >= thr)
    if mask.sum() == 0:
        return None
    rows, cols = np.nonzero(mask)
    return {
        "lat": float(lat_centres[rows].mean()),
        "lon": float(lon_centres[cols].mean()),
        "cells": int(mask.sum()),
    }


def km_between(a, b):
    """Great-circle km between two {lat, lon} points; None if either is missing."""
    if a is None or b is None:
        return None
    p1, p2 = np.radians(a["lat"]), np.radians(b["lat"])
    dl = np.radians(b["lon"] - a["lon"])
    x = np.sin((p2 - p1) / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return float(2 * 6371.0 * np.arcsin(np.sqrt(x)))


# ------------------------------------------------------------------ the grids

def display_grid(hero, manifest_path: pathlib.Path | None = None, layer_id: str = "refc-fine"):
    """The site's grid for this hero store, rebuilt the way the encoder builds it.

    With a manifest given, the rebuilt rect and size are asserted equal to the
    shipped layer's, so the scored grid and the displayed grid cannot diverge.
    Returns (grid, hero_lat, hero_lon) with lon wrapped to -180..180.
    """
    hlat = np.asarray(hero["lat"])
    hlon = to_180(np.asarray(hero["lon"]))
    grid = regrid.target_from_bbox(hlat, hlon)
    if manifest_path is not None:
        m = json.loads(pathlib.Path(manifest_path).read_text(encoding="utf-8"))["layers"][layer_id]
        if [round(v, 4) for v in grid.rect] != [round(v, 4) for v in m["rect"]] or grid.size != m["size"]:
            raise ValueError(
                f"rebuilt grid {grid.rect} {grid.size} does not match the shipped {layer_id} "
                f"{m['rect']} {m['size']} — the verification grid must be the display grid"
            )
    return grid, hlat, hlon


def forecast_on_grid(hero, hlat, hlon, grid, name: str = "hero_refc", sample: int | None = None) -> np.ndarray:
    """Every frame of one hero variable regridded onto `grid`. NaN outside the footprint.

    `sample` selects along a sample axis when the store has one ([1, n, S, y, x]);
    None expects [1, n, y, x].
    """
    idx = regrid.build_index(hlat, hlon, grid)
    arr = hero[name]
    n = arr.shape[1]
    frames = []
    for i in range(n):
        field = np.asarray(arr[0, i] if sample is None else arr[0, i, sample])
        frames.append(idx.apply(field))
    return np.stack(frames).astype(np.float32)


def mrms_on_grid(npz_path: pathlib.Path, grid):
    """MRMS frames (from fetch_mrms) on `grid` by nearest cell, sentinels resolved.

    Returns (obs [n, H, W] with NaN outside radar coverage, valid ISO times,
    per-frame offsets in seconds from the top of the hour).
    """
    m = np.load(npz_path)
    mlat, mlon = m["lat"], to_180(m["lon"])
    ri = np.abs(mlat[None, :] - grid.lat_centres()[:, None]).argmin(axis=1)
    ci = np.abs(mlon[None, :] - grid.lon_centres()[:, None]).argmin(axis=1)
    raw = m["refc_half_dbz"].astype(np.float32) / 2.0
    obs = raw[:, ri][:, :, ci]
    nocov = obs == MRMS_NO_COVERAGE
    obs = np.where(obs == MRMS_NO_ECHO, 0.0, obs)
    obs[nocov] = np.nan
    return obs, [str(v) for v in m["valid"]], np.asarray(m["offset_s"])


# ------------------------------------------------------------------ the scoring

HEADLINE_RULE = "mean-v2"          # stamped into every headline; DOCS/Verification-Protocol.md
COVERAGE_FLOOR = 0.5               # an hour with less radar than this over the grid is not scored


def _defined(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def headline(results: dict, threshold: float = 40.0, spin_up_leads: int = 2) -> dict:
    """The one honest figure a viewer should see without opening the report.

    Rule "mean-v2" (DOCS/Verification-Protocol.md). Over the SCORABLE hours, the
    useful scale is the smallest neighbourhood at which the MEAN FSS reaches the
    mean useful line (Roberts & Lean 2008: 0.5 + f0/2, averaged over the same
    hours). The number of hours individually above the line at that scale is
    reported beside it, never instead of it.

    Why an aggregate, not "any hour": the previous rule named the smallest
    neighbourhood at which at least ONE hour cleared the line, so one chance
    crossing at the coarsest window tested was headlined as "useful at 98 km"
    (it happened on 4 and 5 Sep 2026). Why the mean, not the median: FSS is
    conventionally averaged over cases in the literature, and a median would
    hide the shape of the distribution the hour count already shows.

    An hour is scorable when its FSS is defined (some 40 dBZ mass in forecast or
    radar; a correct null forecast is undefined, not wrong) and radar covers at
    least COVERAGE_FLOOR of the grid. The first `spin_up_leads` hours are never
    scored: a convection-allowing model handed an analysis scores trivially well
    against it before it has done any work.

    status: "useful" (usefulScaleKm set), "below-line" (below at every scale up
    to largestScaleKm, hour count still reported at the largest scale), or
    "no-echo" (nothing scorable at all). All three are real, publishable
    answers and must be rendered, never quietly omitted.
    """
    key = str(int(threshold))
    windows = results["windows_km"]
    post = [r for r in results["leads"] if r["lead_h"] >= spin_up_leads]
    scorable = _scorable(results, key, spin_up_leads)
    base = {
        "rule": HEADLINE_RULE,
        "thresholdDbz": int(threshold),
        "scoredHours": len(scorable),
        "undefinedHours": len(post) - len(scorable),
        "largestScaleKm": round(windows[-1], 1),
    }
    if not scorable:
        return {**base, "status": "no-echo", "usefulScaleKm": None, "usefulHours": 0,
                "meanFss": None, "usefulLine": None}

    line = _useful_line(scorable, key)
    for i, km in enumerate(windows):
        mean, hours = _at(scorable, key, i)
        if mean >= line:
            return {**base, "status": "useful", "usefulScaleKm": round(km, 1), "usefulHours": hours,
                    "meanFss": round(mean, 3), "usefulLine": round(line, 3)}
    mean, hours = _at(scorable, key, len(windows) - 1)
    return {**base, "status": "below-line", "usefulScaleKm": None, "usefulHours": hours,
            "meanFss": round(mean, 3), "usefulLine": round(line, 3)}


def _scorable(results: dict, key: str, spin_up_leads: int) -> list[dict]:
    ncell = int(results["grid"]["size"][0]) * int(results["grid"]["size"][1])
    return [
        r for r in results["leads"]
        if r["lead_h"] >= spin_up_leads
        and r["valid_cells"] / ncell >= COVERAGE_FLOOR
        and all(_defined(v) for v in r["fss"][key]["by_window"])
    ]


def _useful_line(rows: list[dict], key: str) -> float:
    return float(np.mean([r["fss"][key]["fss_useful"] for r in rows]))


def _at(rows: list[dict], key: str, i: int) -> tuple[float, int]:
    """(mean FSS, hours individually above the line) at window index i."""
    vals = [r["fss"][key]["by_window"][i] for r in rows]
    hours = sum(1 for r in rows if r["fss"][key]["by_window"][i] >= r["fss"][key]["fss_useful"])
    return float(np.mean(vals)), hours


def comparators(results: dict, threshold: float = 40.0, spin_up_leads: int = 2) -> dict:
    """Attach the baselines' figures at the forecast's headline scale.

    Each entry of results["baselines"] (persistence, hrrr) is a score() dict of
    its own. Every one is summarised under the same rule as the forecast, and
    its mean FSS is also taken at the SCALE THE FORECAST'S HEADLINE NAMES (the
    useful scale, or the largest tested when below the line), over that
    baseline's own scorable hours, so "the forecast scored 0.43 here; HRRR 0.41;
    persistence 0.30" is one comparison at one scale. Writes
    results["headline"]["comparators"] and returns it; nothing when there are
    no baselines, so older results files are untouched.
    """
    baselines = results.get("baselines") or {}
    if not baselines:
        return {}
    head = results["headline"]
    windows = results["windows_km"]
    scale_km = head["usefulScaleKm"] if head["usefulScaleKm"] is not None else windows[-1]
    i = min(range(len(windows)), key=lambda k: abs(windows[k] - scale_km))
    key = str(int(threshold))
    out = {}
    for name, b in baselines.items():
        own = headline(b, threshold, spin_up_leads)
        rows = _scorable(b, key, spin_up_leads)
        mean, hours = _at(rows, key, i) if rows else (None, 0)
        out[name] = {
            "scaleKm": round(windows[i], 1),
            "meanFss": None if mean is None else round(mean, 3),
            "usefulHours": hours,
            "scoredHours": len(rows),
            "status": own["status"],
            "usefulScaleKm": own["usefulScaleKm"],
        }
        b["headline"] = own
    head["comparators"] = out
    return out


def hrrr_on_grid(npz_path: pathlib.Path, grid) -> tuple[np.ndarray, list[str]]:
    """HRRR composite reflectivity frames (from fetch_hrrr_refc) on `grid` by
    nearest cell, NaN outside the crop and where the archive had no value."""
    m = np.load(npz_path)
    idx = regrid.build_index(m["lat"].astype(np.float64), to_180(m["lon"].astype(np.float64)), grid)
    raw = m["refc_half_dbz"].astype(np.float32) / 2.0
    raw[raw <= -900.0] = np.nan
    frames = np.stack([idx.apply(raw[i]) for i in range(raw.shape[0])]).astype(np.float32)
    return frames, [str(v) for v in m["valid"]]


def score(
    fc: np.ndarray,
    obs: np.ndarray,
    times: list[str],
    grid,
    thresholds: list[float] = (20.0, 30.0, 40.0),
    windows_px: list[int] = (1, 5, 11, 21, 41),
    members: np.ndarray | None = None,
) -> dict:
    """Per-lead FSS, coverage, centroid separation and maxima for one forecast.

    `fc` is the field scored as the deterministic forecast ([n, H, W]). With
    `members` ([M, n, H, W]) also given, the ensemble probability of exceedance is
    scored with fss_probabilistic() and the member spread is reported alongside.
    """
    px_km_x = (grid.east - grid.west) / grid.width * KM_PER_DEG * np.cos(np.radians((grid.south + grid.north) / 2))
    px_km_y = (grid.north - grid.south) / grid.height * KM_PER_DEG
    px_km = float((px_km_x + px_km_y) / 2)
    lat_c, lon_c = grid.lat_centres(), grid.lon_centres()

    results = {
        "grid": {"rect": grid.rect, "size": grid.size, "km_per_px": px_km},
        "thresholds_dbz": list(thresholds),
        "windows_px": list(windows_px),
        "windows_km": [round(w * px_km, 1) for w in windows_px],
        "init": times[0],
        "members": None if members is None else int(members.shape[0]),
        "leads": [],
    }
    for h in range(fc.shape[0]):
        v = np.isfinite(fc[h]) & np.isfinite(obs[h])
        row = {"lead_h": h, "valid": times[h], "valid_cells": int(v.sum()), "fss": {}, "coverage": {}}
        for thr in thresholds:
            f0 = coverage(obs[h], v, thr)
            entry = {
                "by_window": [fss(fc[h], obs[h], v, thr, w) for w in windows_px],
                "obs_base_rate": f0,
                "fss_useful": 0.5 + f0 / 2.0,
                "fss_random": f0,
            }
            if members is not None:
                prob = np.mean(members[:, h] >= thr, axis=0)
                entry["ensemble_by_window"] = [fss_probabilistic(prob, obs[h], v, thr, w) for w in windows_px]
                entry["member_by_window"] = [
                    [fss(members[k, h], obs[h], v, thr, w) for w in windows_px] for k in range(members.shape[0])
                ]
            row["fss"][str(int(thr))] = entry
            row["coverage"][str(int(thr))] = {"forecast": coverage(fc[h], v, thr), "observed": f0}
            if members is not None:
                row["coverage"][str(int(thr))]["members"] = [coverage(members[k, h], v, thr) for k in range(members.shape[0])]
        cf, co = centroid(fc[h], v, lat_c, lon_c, 40.0), centroid(obs[h], v, lat_c, lon_c, 40.0)
        row["centroid_40dbz"] = {"forecast": cf, "observed": co, "separation_km": km_between(cf, co)}
        row["max_dbz"] = {"forecast": float(np.nanmax(fc[h])), "observed": float(np.nanmax(obs[h]))}
        results["leads"].append(row)
    return results
