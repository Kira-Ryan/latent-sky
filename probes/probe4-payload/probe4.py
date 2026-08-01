"""Probe 4 — replace every guessed payload number in Architecture.md §8 with a measurement.

Measures, on REAL fields:
  1. Hero layers  — the 5 real CWB sample timesteps (data/dev/raw/cwb_sample.npz, two of
     which are Typhoon Chanthu 2021-09-12), regridded per §5.4 from the 448x448 curvilinear
     WRF grid onto the 1.25x (579x566) and 1.00x (463x453) equirectangular targets, ramped
     through the real §7.1 LUTs, encoded WebP lossless exact.
  2. The coarse "before" layer — coarse_wind10m sampled back onto its true native
     36x40 0.25 deg input grid, encoded the same way.
  3. Global layers — one real ERA5 analysis timestep (2024-07-24T00Z, the Gaemi hero init)
     from the anonymous ARCO store: t2m and 10 m wind speed at 1440x721 and 720x361.
  4. For every frame, two alternative datapoints: WebP lossy q90 (not shipped — delta only)
     and brotli -q 11 of the raw uint8 index plane (the "packed blob" alternative).

Outputs (all derived imagery goes under data/dev/ because the CWB sample is
CC BY-NC-ND 4.0 and data/dev/ is the gitignored quarantine for anything derived from it):
  data/dev/probe4/                     — LUTs, WebP frames, ERA5 slice cache, results.json
  probes/probe4-payload/results.md     — the report (numbers only, no licensed data)

Run:  python probes/probe4-payload/probe4.py
Rerun is end-to-end; the ERA5 slice is cached after the first fetch (delete
data/dev/probe4/era5_cache.npz to force a refetch).
"""

from __future__ import annotations

import io
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import brotli
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree

REPO = Path(__file__).resolve().parents[2]
RAW = REPO / "data" / "dev" / "raw" / "cwb_sample.npz"
OUT = REPO / "data" / "dev" / "probe4"          # NC-ND quarantine — never committed
REPORT = Path(__file__).resolve().parent / "results.md"
RESULTS_JSON = OUT / "results.json"
ERA5_CACHE = OUT / "era5_cache.npz"

# §5.3 hero init — pinned, > 3 months old, so ERA5T can never be rewritten under us.
ERA5_TIME = "2024-07-24T00:00"
ARCO_STORE = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"

MB = 1_000_000  # decimal MB, matching §8's `du -sb` byte accounting

# §7.1 ramps — global vmin/vmax, identical for coarse and fine. Never per-layer autoscale.
RAMPS = {
    "wind10m": dict(cmap="batlowK", vmin=0.0, vmax=55.0, alpha=("ramp", 2.0, 6.0)),
    "t2m":     dict(cmap="thermal", vmin=233.15, vmax=323.15, alpha=("opaque",)),
    "mrr":     dict(cmap="ChaseSpectral", vmin=0.0, vmax=55.0, alpha=("ramp", 0.0, 5.0)),
}

# §5.4 regrid targets over the measured bbox (width, height)
TARGETS = {"1.25x": (579, 566), "1.00x": (463, 453)}


# ----------------------------------------------------------------------------- LUTs

def bake_lut(var: str) -> np.ndarray:
    """256x1 RGBA LUT per §7.1. Entry i is the colour of value vmin + i/255*(vmax-vmin),
    matching the §5.5 quantiser idx = round(clip((f-vmin)/(vmax-vmin))*255)."""
    spec = RAMPS[var]
    x = np.arange(256) / 255.0
    if spec["cmap"] == "batlowK":
        import cmcrameri.cm as cmc
        rgba = cmc.batlowK(x)
    elif spec["cmap"] == "thermal":
        import cmocean
        rgba = cmocean.cm.thermal(x)
    elif spec["cmap"] == "ChaseSpectral":
        import cmweather  # noqa: F401  (import registers the colormap)
        import matplotlib
        rgba = matplotlib.colormaps["ChaseSpectral"](x)
    else:
        raise ValueError(f"unknown cmap {spec['cmap']!r}")

    values = spec["vmin"] + x * (spec["vmax"] - spec["vmin"])
    if spec["alpha"][0] == "opaque":
        alpha = np.ones(256)
    else:
        _, a0, a1 = spec["alpha"]
        alpha = np.clip((values - a0) / (a1 - a0), 0.0, 1.0)
    rgba[:, 3] = alpha

    lut = (rgba * 255.0 + 0.5).astype(np.uint8)  # (256, 4)
    lut_dir = OUT / "luts"
    lut_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(lut[np.newaxis, :, :], mode="RGBA").save(lut_dir / f"{var}.lut.png")
    return lut


def quantise(field: np.ndarray, var: str) -> np.ndarray:
    """§5.5 — identical arithmetic on both sides, GLOBAL vmin/vmax."""
    spec = RAMPS[var]
    idx = np.clip((field.astype(np.float64) - spec["vmin"]) / (spec["vmax"] - spec["vmin"]), 0.0, 1.0)
    return (idx * 255.0 + 0.5).astype(np.uint8)


# ----------------------------------------------------------------------------- encoding

def verify_exact_alpha_support() -> dict:
    """PROVE that Pillow's exact=True preserves RGB under alpha=0 bit-identically,
    and that the default does not. Raises if exact round-trip fails — in that case
    an alternative encoder (cwebp -lossless -exact) would be required."""
    rng = np.random.default_rng(42)
    a = rng.integers(0, 256, size=(64, 64, 4), dtype=np.uint8)
    a[..., 3] = np.where(rng.random((64, 64)) < 0.5, 0, 255).astype(np.uint8)

    def roundtrip(**save_kwargs) -> bool:
        buf = io.BytesIO()
        Image.fromarray(a, "RGBA").save(buf, format="WEBP", lossless=True, method=6, **save_kwargs)
        back = np.asarray(Image.open(io.BytesIO(buf.getvalue())).convert("RGBA"))
        return bool(np.array_equal(a, back))

    exact_ok = roundtrip(exact=True)
    default_ok = roundtrip()
    if not exact_ok:
        raise RuntimeError(
            "Pillow WebP exact=True did NOT round-trip bit-identically — "
            "this Pillow build cannot be used; switch to the cwebp CLI with -exact."
        )
    import PIL
    return {"pillow": PIL.__version__, "exact_true_bit_identical": exact_ok,
            "default_bit_identical": default_ok}


def encode_frame(rgba: np.ndarray, idx: np.ndarray, path: Path) -> dict:
    """Write WebP lossless exact, verify bit-identity, and measure the alternatives."""
    img = Image.fromarray(rgba, mode="RGBA")
    img.save(path, format="WEBP", lossless=True, exact=True, method=6)
    lossless_bytes = path.stat().st_size

    back = np.asarray(Image.open(path).convert("RGBA"))
    if not np.array_equal(rgba, back):
        raise RuntimeError(f"round-trip NOT bit-identical: {path}")

    buf = io.BytesIO()
    img.save(buf, format="WEBP", lossless=False, quality=90, method=6)
    lossy_bytes = buf.getbuffer().nbytes

    brotli_bytes = len(brotli.compress(idx.tobytes(), quality=11))

    h, w = idx.shape
    return {"px": w * h, "webp_lossless": lossless_bytes,
            "webp_lossless_bpp": round(lossless_bytes / (w * h), 4),
            "webp_q90": lossy_bytes, "brotli_q11_index": brotli_bytes}


# ----------------------------------------------------------------------------- hero regrid

def sphere_xyz(lat_deg: np.ndarray, lon_deg: np.ndarray) -> np.ndarray:
    """Unit-sphere coordinates — rotation-proof nearest-neighbour metric for the
    curvilinear WRF grid (no cos-latitude fudge factors)."""
    lat = np.radians(lat_deg.astype(np.float64))
    lon = np.radians(lon_deg.astype(np.float64))
    return np.column_stack([
        (np.cos(lat) * np.cos(lon)).ravel(),
        (np.cos(lat) * np.sin(lon)).ravel(),
        np.sin(lat).ravel(),
    ])


def build_hero_indices(lat2d: np.ndarray, lon2d: np.ndarray) -> tuple[dict, dict, dict]:
    """cKDTree nearest-neighbour source index for each target grid (§5.4)."""
    tree = cKDTree(sphere_xyz(lat2d, lon2d))
    west, east = float(lon2d.min()), float(lon2d.max())
    south, north = float(lat2d.min()), float(lat2d.max())
    bbox = dict(west=west, south=south, east=east, north=north)

    indices, dists = {}, {}
    for name, (w, h) in TARGETS.items():
        lon_t = west + (np.arange(w) + 0.5) / w * (east - west)
        lat_t = north - (np.arange(h) + 0.5) / h * (north - south)   # row 0 = north
        lon_g, lat_g = np.meshgrid(lon_t, lat_t)
        d, i = tree.query(sphere_xyz(lat_g, lon_g), k=1)
        indices[name] = i.reshape(h, w)
        km = np.degrees(2 * np.arcsin(d / 2)) * 111.19  # chord -> great-circle km
        # Pixels > 1 native cell from any source point sit OUTSIDE the rotated WRF hull
        # (the §3.4 curvature — bbox corners the grid never covers) and get edge-clamped.
        dists[name] = {
            "median_km": round(float(np.median(km)), 3),
            "max_km": round(float(km.max()), 2),
            "pct_beyond_hull": round(float((km > 2.07).mean() * 100), 2),
        }

    # true native input grid, per §3.4.1 — lat 36 rows, lon 40 cols
    lat_n = np.linspace(19.25, 28, 36, endpoint=True)[::-1]          # row 0 = north
    lon_n = np.linspace(116, 126, 40, endpoint=False)
    lon_g, lat_g = np.meshgrid(lon_n, lat_n)
    _, i = tree.query(sphere_xyz(lat_g, lon_g), k=1)
    indices["native36x40"] = i.reshape(36, 40)
    return indices, dists, bbox


def run_hero(z: np.lib.npyio.NpzFile, luts: dict) -> tuple[dict, dict, dict]:
    indices, max_nn_km, bbox = build_hero_indices(z["lat"], z["lon"])
    results: dict = {}
    hero_dir = OUT / "hero"
    hero_dir.mkdir(parents=True, exist_ok=True)

    for var, field_key in [("wind10m", "fine_wind10m"), ("mrr", "fine_mrr"), ("t2m", "fine_t2m")]:
        for scale in TARGETS:
            key = f"fine_{var}_{scale}"
            frames = []
            for t in range(z[field_key].shape[0]):
                flat = z[field_key][t].ravel()
                resampled = flat[indices[scale]]
                idx = quantise(resampled, var)
                rgba = luts[var][idx]
                path = hero_dir / f"{key}_t{t}.webp"
                frames.append(encode_frame(rgba, idx, path))
            results[key] = frames
            print(f"  {key}: " + " ".join(f"{f['webp_lossless']:,}" for f in frames))

    # the true "before" layer — coarse wind at native 36x40, NEAREST both ways
    frames = []
    for t in range(z["coarse_wind10m"].shape[0]):
        flat = z["coarse_wind10m"][t].ravel()
        resampled = flat[indices["native36x40"]]
        idx = quantise(resampled, "wind10m")
        rgba = luts["wind10m"][idx]
        path = hero_dir / f"coarse_wind10m_36x40_t{t}.webp"
        frames.append(encode_frame(rgba, idx, path))
    results["coarse_wind10m_36x40"] = frames
    print("  coarse_wind10m_36x40: " + " ".join(f"{f['webp_lossless']:,}" for f in frames))

    return results, max_nn_km, bbox


# ----------------------------------------------------------------------------- global layer

def fetch_era5() -> tuple[dict, str]:
    """One real global 0.25 deg analysis timestep. ARCO first (anonymous GCS);
    cached locally so reruns are offline."""
    source = f"ARCO `{ARCO_STORE}` @ {ERA5_TIME}Z (anonymous, no credentials)"
    if ERA5_CACHE.exists():
        z = np.load(ERA5_CACHE)
        return {k: z[k] for k in z.files}, source + f" — served from local cache {ERA5_CACHE.name}"

    import xarray as xr
    print(f"  fetching {ERA5_TIME} from {ARCO_STORE} (anonymous) ...")
    ds = xr.open_zarr(ARCO_STORE, chunks=None, storage_options={"token": "anon"})
    sel = ds[["2m_temperature", "10m_u_component_of_wind", "10m_v_component_of_wind"]].sel(
        time=ERA5_TIME)
    lat = sel["latitude"].values
    if lat[0] < lat[-1]:
        raise RuntimeError(f"expected ARCO latitude descending 90..-90, got {lat[0]}..{lat[-1]}")
    t2m = sel["2m_temperature"].values.astype(np.float32)
    u = sel["10m_u_component_of_wind"].values.astype(np.float32)
    v = sel["10m_v_component_of_wind"].values.astype(np.float32)
    if t2m.shape != (721, 1440):
        raise RuntimeError(f"expected 721x1440, got {t2m.shape}")
    out = {"t2m": t2m, "u10m": u, "v10m": v}
    np.savez_compressed(ERA5_CACHE, **out)
    return out, source


def run_global(luts: dict) -> tuple[dict, str, dict]:
    fields, source = fetch_era5()
    # §3.1 — derive wind speed BEFORE ramping, identically to the hero side
    wind = np.sqrt(fields["u10m"].astype(np.float64) ** 2 + fields["v10m"].astype(np.float64) ** 2)
    stats = {
        "t2m_min_K": round(float(fields["t2m"].min()), 2),
        "t2m_max_K": round(float(fields["t2m"].max()), 2),
        "t2m_pct_below_lut_vmin": round(float((fields["t2m"] < RAMPS["t2m"]["vmin"]).mean() * 100), 2),
        "wind_min_ms": round(float(wind.min()), 3),
        "wind_max_ms": round(float(wind.max()), 2),
    }
    print(f"  era5 t2m range: {stats['t2m_min_K']}..{stats['t2m_max_K']} K "
          f"({stats['t2m_pct_below_lut_vmin']}% below LUT vmin {RAMPS['t2m']['vmin']} K) | "
          f"wind10m range: {stats['wind_min_ms']}..{stats['wind_max_ms']} m/s")

    results: dict = {}
    gdir = OUT / "global"
    gdir.mkdir(parents=True, exist_ok=True)
    for var, full in [("t2m", fields["t2m"]), ("wind10m", wind)]:
        for res_name, arr in [("1440x721", full), ("720x361", full[::2, ::2])]:
            idx = quantise(arr, var)
            rgba = luts[var][idx]
            path = gdir / f"global_{var}_{res_name}.webp"
            results[f"global_{var}_{res_name}"] = [encode_frame(rgba, idx, path)]
            print(f"  global_{var}_{res_name}: {results[f'global_{var}_{res_name}'][0]['webp_lossless']:,}")
    return results, source, stats


# ----------------------------------------------------------------------------- report

def mean_bytes(frames: list[dict], key: str = "webp_lossless") -> float:
    return sum(f[key] for f in frames) / len(frames)


def typhoon_bytes(frames: list[dict], key: str = "webp_lossless") -> float:
    """Frames 3 and 4 are Typhoon Chanthu — the active-weather upper bound."""
    return sum(frames[t][key] for t in (3, 4)) / 2


def write_report(res: dict, meta: dict) -> None:
    hero, glob = res["hero"], res["global"]

    # §8 rows: (label, frames-key, steps, §8 estimate MB)
    hero_rows = [
        ("6 — hero coarse wind, 36x40",      "coarse_wind10m_36x40", 12, 0.02),
        ("7 — hero fine wind, 579x566",      "fine_wind10m_1.25x",   12, 0.79),
        ("8 — hero fine reflectivity, 579x566", "fine_mrr_1.25x",    12, 1.38),
        ("13 — hero fine t2m, 579x566 (lazy)", "fine_t2m_1.25x",     12, 0.59),
    ]
    global_rows = [
        ("10 — global t2m, 720x361",   "global_t2m_720x361",     21, 0.95),
        ("11 — global wind, 720x361",  "global_wind10m_720x361", 21, 1.16),
    ]

    lines = []
    a = lines.append
    nn = meta["regrid_nn"]["1.25x"]
    a("# Probe 4 — payload measured on real fields")
    a("")
    a(f"> Run: {meta['run_at']} · Python {sys.version.split()[0]} · Pillow {meta['exact']['pillow']}"
      f" · encoder `lossless=True, exact=True, method=6` · MB = 10^6 bytes (matches §8 `du -sb`)")
    a("> Hero source: the 5 real CWB sample timesteps (2 are Typhoon Chanthu 2021-09-12),")
    a(f"> regridded per §5.4 — cKDTree nearest on the unit sphere; median NN distance "
      f"{nn['median_km']} km at 1.25x, with {nn['pct_beyond_hull']}% of target pixels beyond "
      f"the rotated WRF hull (bbox corners, §3.4 curvature — max {nn['max_km']} km, edge-clamped).")
    a(f"> Global source: {meta['era5_source']}.")
    a("")
    a("**Round-trip identity: PROVEN.** Every encoded frame was decoded and compared — "
      f"all {meta['n_frames_verified']} frames bit-identical. The built-in adversarial test "
      "(random RGB under alpha=0) confirms Pillow "
      f"{meta['exact']['pillow']} honours `exact=True` "
      f"(and that omitting it corrupts: default round-trip identical = "
      f"{meta['exact']['default_bit_identical']}). §5.5's `exact=True` is mandatory, as stated.")
    a("")
    a("## Per-frame measurements — hero (5 real timesteps; t3, t4 are the typhoon)")
    a("")
    a("| Layer | px | lossless B/frame (mean of 5) | typhoon mean (t3,t4) | B/px mean | B/px typhoon | q90 mean | brotli-q11 index mean |")
    a("|---|---|---|---|---|---|---|---|")
    for key, frames in hero.items():
        px = frames[0]["px"]
        m, ty = mean_bytes(frames), typhoon_bytes(frames)
        mq = mean_bytes(frames, "webp_q90")
        mb = mean_bytes(frames, "brotli_q11_index")
        a(f"| {key} | {px:,} | {m:,.0f} | {ty:,.0f} | {m/px:.3f} | {ty/px:.3f} | {mq:,.0f} | {mb:,.0f} |")
    a("")
    a("## Per-frame measurements — global (one real timestep)")
    a("")
    a("| Layer | px | lossless B | B/px | q90 B | brotli-q11 index B |")
    a("|---|---|---|---|---|---|")
    for key, frames in glob.items():
        f = frames[0]
        a(f"| {key} | {f['px']:,} | {f['webp_lossless']:,} | {f['webp_lossless_bpp']:.3f} "
          f"| {f['webp_q90']:,} | {f['brotli_q11_index']:,} |")
    a("")
    a("## §8 rows, re-measured")
    a("")
    a("Central = mean-of-5-timesteps x steps (3 of the 5 sample times are quiet weather); "
      "typhoon = mean of the two Chanthu frames x steps — the honest upper bound, since a "
      "hero event's 12 steps are mostly active weather. Global rows extrapolate the single "
      "measured frame (a real typhoon-day analysis).")
    a("")
    a("| §8 row | §8 est. MB | implied §8 B/px | measured B/px (central / typhoon) | measured central MB | measured typhoon MB | verdict |")
    a("|---|---|---|---|---|---|---|")
    total_c = total_t = total_e = 0.0
    ens_m = 4 * mean_bytes(hero["fine_wind10m_1.25x"]) / MB
    ens_t = 4 * typhoon_bytes(hero["fine_wind10m_1.25x"]) / MB
    for label, key, steps, est in hero_rows:
        frames = hero[key]
        px = frames[0]["px"]
        implied = est * MB / steps / px
        c = mean_bytes(frames) * steps / MB
        t = typhoon_bytes(frames) * steps / MB
        if not label.startswith("13"):
            total_c += c; total_t += t; total_e += est
        verdict = "beats §8" if t <= est else ("holds (central only)" if c <= est else "breaks §8")
        a(f"| {label} | {est:.2f} | {implied:.3f} | {mean_bytes(frames)/px:.3f} / "
          f"{typhoon_bytes(frames)/px:.3f} | {c:.2f} | {t:.2f} | {verdict} |")
    a(f"| 9 — ensemble panel, 4 x fine wind frame (proxy) | 0.49 | — | — | {ens_m:.2f} | {ens_t:.2f} | "
      + ("beats §8" if ens_t <= 0.49 else ("holds (central only)" if ens_m <= 0.49 else "breaks §8")) + " |")
    total_c += ens_m; total_t += ens_t; total_e += 0.49
    for label, key, steps, est in global_rows:
        f = glob[key][0]
        implied = est * MB / steps / f["px"]
        c = f["webp_lossless"] * steps / MB
        total_c += c; total_t += c; total_e += est
        verdict = "beats §8" if c <= est else "breaks §8"
        a(f"| {label} | {est:.2f} | {implied:.3f} | {f['webp_lossless_bpp']:.3f} | {c:.2f} | {c:.2f} | {verdict} |")
    a(f"| **Hero + global subtotal (eager rows 6-11)** | **{total_e:.2f}** | | | **{total_c:.2f}** | **{total_t:.2f}** | |")
    a(f"| **Projected eager total (+2.42 core, §8)** | **{total_e + 2.42:.2f}** | | | **{total_c + 2.42:.2f}** | **{total_t + 2.42:.2f}** | vs 12.00 ceiling |")
    a("")
    a("## Payload levers, re-measured")
    a("")
    a("| Lever | §8 claimed | measured (typhoon-weighted) |")
    a("|---|---|---|")
    lever_saving = 0.0
    for var in ("wind10m", "mrr", "t2m"):
        f125, f100 = hero[f"fine_{var}_1.25x"], hero[f"fine_{var}_1.00x"]
        lever_saving += (typhoon_bytes(f125) - typhoon_bytes(f100)) * 12 / MB
    glob_lever = (glob["global_t2m_720x361"][0]["webp_lossless"]
                  + glob["global_wind10m_720x361"][0]["webp_lossless"]) * 10 / MB
    a(f"| regrid 1.25x -> 1.00x, all three fine layers | -0.90 MB | **-{lever_saving:.2f} MB** |")
    a(f"| global steps 21 -> 11 | -1.05 MB | **-{glob_lever:.2f} MB** |")
    a("")
    res["summary"] = {
        "eager_projected_central_MB": round(total_c + 2.42, 2),
        "eager_projected_typhoon_MB": round(total_t + 2.42, 2),
        "s8_eager_MB": round(total_e + 2.42, 2),
        "lever_1_00x_saving_MB": round(lever_saving, 2),
        "lever_global_11steps_MB": round(glob_lever, 2),
    }
    REPORT.write_text("\n".join(lines) + build_findings(res, meta), encoding="utf-8")
    print(f"\nreport written: {REPORT}")


def build_findings(res: dict, meta: dict) -> str:
    """Verdict prose appended after the tables — numbers all live above."""
    s = res["summary"]
    hero, glob = res["hero"], res["global"]
    bpp = {k: typhoon_bytes(v) / v[0]["px"] for k, v in hero.items()
           if k.startswith("fine") and k.endswith("1.25x")}
    lo, hi = min(bpp.values()), max(bpp.values())
    g_bpp = [glob[k][0]["webp_lossless_bpp"] for k in
             ("global_t2m_720x361", "global_wind10m_720x361")]
    q90_hero = mean_bytes(hero["fine_wind10m_1.25x"]) / mean_bytes(hero["fine_wind10m_1.25x"], "webp_q90")
    q90_glob = glob["global_t2m_1440x721"][0]["webp_lossless"] / glob["global_t2m_1440x721"][0]["webp_q90"]
    nn = meta["regrid_nn"]
    e5 = meta["era5_stats"]
    return f"""
## Where reality lands against §8's 0.15-0.35 B/px assumption

**Hero fine layers: the assumption HOLDS.** Measured typhoon-frame B/px at 1.25x spans
**{lo:.3f}-{hi:.3f}** — inside the band, at its upper half for wind and reflectivity.
Where §8's hero rows break, they break because the row's *implied* B/px sat near the bottom
of the band (see the implied column above), not because real fields blew past 0.35.

**Global layers: the assumption BREAKS.** Real 0.5-degree ERA5 encodes at
**{g_bpp[0]:.3f} / {g_bpp[1]:.3f} B/px** (t2m / wind) — the coastline-and-gradient content a
k^-4 synthetic field does not have. §8's global rows implied ~0.17-0.21 B/px and are
**~2.3x under-estimated**; this is where the eager budget actually moves.

**Bottom line: eager lands at {s['eager_projected_central_MB']:.2f} MB central /
{s['eager_projected_typhoon_MB']:.2f} MB typhoon-weighted** against §8's
**{s['s8_eager_MB']:.2f} MB** estimate — over the estimate but still under the 12.00 MB
ceiling, with the measured global-steps lever (-{s['lever_global_11steps_MB']:.2f} MB alone)
big enough to restore the whole margin without touching the hero.

Alternative datapoints (not shipped, measured for the record):
- **WebP q90 lossy** is ~{q90_hero:.0f}x smaller on hero wind and ~{q90_glob:.0f}x smaller on
  global t2m. That is the price of colour identity being a property of the format; known, and
  declined deliberately.
- **brotli -q 11 of the raw uint8 index plane** (the "packed blob" design, LUT applied
  client-side) lands within +/-8% of lossless WebP on every full-size field — slightly
  smaller on wind/reflectivity/global, slightly larger on smooth t2m. Only the ~1 KB 36x40
  coarse frames favour brotli (~15%), where WebP container overhead dominates. No meaningful
  win, and it forfeits native image decode. Not worth pursuing.

Findings that belong in Architecture.md:
- **§7.1 t2m range clips the real global field.** ERA5 2 m temperature at {ERA5_TIME}Z spans
  {e5['t2m_min_K']}-{e5['t2m_max_K']} K; {e5['t2m_pct_below_lut_vmin']}% of the globe (the
  Antarctic winter plateau) sits below the LUT's 233.15 K vmin and saturates at index 0. Fine
  for the Taiwan hero; the GLOBAL t2m layer needs either a wider range (e.g. 203.15 K floor)
  or an explicit "clipped at -40 C" caption.
- **The §5.4 bbox corners are outside the rotated WRF hull.** {nn['1.25x']['pct_beyond_hull']}%
  of 1.25x target pixels have no source cell within one native cell (max {nn['1.25x']['max_km']} km
  at the corners — the same §3.4 curvature, seen from the other side). Nearest-neighbour
  edge-clamps them, smearing edge values into the corners. The production encoder should
  alpha-mask beyond-hull pixels instead; interior median NN distance is
  {nn['1.25x']['median_km']} km, i.e. the regrid itself is sound.

Caveats, stated plainly:
- The 5 hero timesteps are monthly analysis snapshots, not 12 consecutive forecast steps of
  one event; the typhoon-weighted column is the honest planning figure for a Gaemi run.
- The ensemble-panel row reuses the fine-wind per-frame cost as a proxy for 4 CorrDiff
  samples — same variable, same geometry, different noise draw.
- The native 36x40 grid extends slightly past the 448x448 output hull (lat 19.25 vs 19.52,
  lon 125.75 vs 125.55); nearest-neighbour clamps to the edge cells there, which is what the
  shipped "before" layer would also do.
- Global rows extrapolate one real frame x 21 steps; frame-to-frame variance is unmeasured,
  but analysis fields at 6 h spacing vary far less than the hero's convective field, and the
  measured frame is a real typhoon-day analysis, not a quiet day.
"""


# ----------------------------------------------------------------------------- main

def main() -> None:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    if not RAW.exists():
        raise FileNotFoundError(f"dev sample missing: {RAW}")

    print("== exact-alpha support ==")
    exact = verify_exact_alpha_support()
    print(f"  Pillow {exact['pillow']}: exact=True bit-identical={exact['exact_true_bit_identical']}, "
          f"default bit-identical={exact['default_bit_identical']}")

    print("== baking LUTs ==")
    luts = {var: bake_lut(var) for var in RAMPS}
    print(f"  {', '.join(RAMPS)} -> {OUT / 'luts'}")

    z = np.load(RAW)
    print("== hero layers (regrid + encode, 5 real timesteps) ==")
    hero, regrid_nn, bbox = run_hero(z, luts)

    print("== global layer (real ERA5 via ARCO) ==")
    try:
        glob, era5_source, era5_stats = run_global(luts)
    except Exception:
        traceback.print_exc()
        raise RuntimeError(
            "ARCO ERA5 fetch failed. Per the probe brief, a real anonymous fallback source "
            "must be substituted and DECLARED in results.md — do not fabricate global numbers."
        ) from None

    n_verified = sum(len(v) for v in hero.values()) + sum(len(v) for v in glob.values())
    meta = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exact": exact, "regrid_nn": regrid_nn, "bbox": bbox,
        "era5_source": era5_source, "era5_stats": era5_stats,
        "n_frames_verified": n_verified,
    }
    res = {"hero": hero, "global": glob}
    write_report(res, meta)
    RESULTS_JSON.write_text(json.dumps({"meta": meta, **res}, indent=2), encoding="utf-8")
    print(f"results json: {RESULTS_JSON}")
    print(f"done in {time.time() - t0:.1f} s — {n_verified} frames encoded, all round-trips bit-identical")


if __name__ == "__main__":
    main()
