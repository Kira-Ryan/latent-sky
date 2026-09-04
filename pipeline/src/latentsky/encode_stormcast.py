"""Encode a StormCast run into web layers — the US convection-allowing event.

Publishable sibling of encode_forecast (which handles SFNO -> CorrDiffTaiwan).
Same LUTs, same grid rule, same layer-id vocabulary, so the viewer renders this
with no code change — but the model pair is different and so is the story.

LICENCE: everything here is publishable. The fine field is StormCast v1 output
(Apache-2.0 checkpoint, hf://nvidia/stormcast-v1-era5-hrrr) initialised from HRRR
and conditioned on GFS, both NOAA public domain. Nothing CC BY-NC-ND is read, and
the equirectangular grid we publish on is one we define (§3.10), so the model's
own curvilinear geolocation arrays are never redistributed.

forecast_stormcast.py writes two stores:
    <out>.zarr        coarse global GFS_FX   u10m v10m t2m tcwv msl  [1, N, 721, 1440]
    <out>_hero.zarr   StormCast 3 km         hero_{u10m,v10m,t2m,refc}
                                             [1, N, 512, 640] + 2-D lat/lon

WHAT MAKES THIS EVENT DIFFERENT FROM TAIWAN
-------------------------------------------
The Taiwan reveal compares a 25 km input against a 2 km diffusion downscaling of
that same input. Here the coarse side is a genuine 25 km GFS FORECAST at the same
init and the same lead time as the 3 km field beside it — a like-for-like forecast
comparison rather than an upscale/downscale pair.

And reflectivity has NO coarse counterpart at all: a global 25 km model does not
produce radar reflectivity, at any resolution, ever. That is the single clearest
statement of what a convection-allowing model buys you, so refc-fine ships without
a pair and says so in its label.

refc uses its OWN ramp (0-75 dBZ), not mrr's (0-55). Reusing mrr would have
retroactively rescaled both shipped Taiwan events, because §7.2b fixes vmin/vmax
globally per variable forever.

ENSEMBLE (optional): --member, repeatable, names the per-member hero stores a
`forecast_stormcast.py --members N` run wrote. They add ONE layer, prob40-fine:
the percentage of members at or above 40 dBZ in each cell, the same threshold
the FSS verification scores. The deterministic refc-fine layer is untouched.

Usage:
    python -m latentsky.encode_stormcast \
        --zarr data/zarr/dixie_2025.zarr \
        --event-config pipeline/configs/event_dixie_2025.yaml \
        --out data/web/dixie --event-id us-dixie-2025 \
        [--mrms data/zarr/mrms_dixie_2025_pre.npz] \
        [--member data/zarr/dixie_2025_pre_ens_m00_hero.zarr --member ...]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import time
from datetime import datetime

import numpy as np
import yaml

from . import basemap as basemap_mod, budget, catalogue as catalogue_mod, regrid
from .encode import LayerRecord, encode_frame, load_lut, make_layer_record
from .encode_global import BASEMAP_REL, GLOBAL_GRID
from .manifest import build_manifest, run_hints, write_manifest
from .ramps import DEFAULT_CONFIG, DEFAULT_LUT_DIR, load_ramps

# GFS is a 0.25 deg global grid, so the coarse cells are ~25 km — the honest
# "before" of the comparison, rendered at its own resolution as well as resampled.
COARSE_STEP_DEG = 0.25


def to_180(lon: np.ndarray) -> np.ndarray:
    """Wrap 0..360 east longitudes into Cesium's -180..180.

    StormCast's grid and the GFS grid are both 0..360, and Cesium's Rectangle
    is -180..180: handing it 250.37..274.58 makes ImageryLayer blow up inside
    _createTileImagerySkeletons with an undefined read, nowhere near the cause.
    Taiwan never exposed this because 116..125 is valid in both conventions.

    Safe here because this domain (-109.6..-85.4) does not straddle the
    antimeridian; a domain that did would need the rect split in two.
    """
    return ((np.asarray(lon) + 180.0) % 360.0) - 180.0


class EncodeStormcastError(RuntimeError):
    """The run could not be encoded. The build must stop."""


PROB_THRESHOLD_DBZ = 40.0


def exceedance_probability(fields: np.ndarray, thr: float = PROB_THRESHOLD_DBZ) -> np.ndarray:
    """Percent of members (axis 0) at or above `thr`, per cell.

    NaN wherever any member is NaN, so a hole in one member never reads as
    "no member exceeded". A single member is not an ensemble and is refused.
    """
    fields = np.asarray(fields, dtype=np.float32)
    if fields.ndim != 3 or fields.shape[0] < 2:
        raise EncodeStormcastError(
            f"an ensemble probability needs [members >= 2, y, x], got {fields.shape}"
        )
    p = 100.0 * np.mean(fields >= thr, axis=0)
    p[np.isnan(fields).any(axis=0)] = np.nan
    return p.astype(np.float32)


def open_stores(coarse_path: pathlib.Path) -> tuple:
    """Open the coarse store and its _hero sibling, written together."""
    import zarr

    coarse_path = pathlib.Path(coarse_path)
    hero_path = coarse_path.with_name(coarse_path.stem + "_hero" + coarse_path.suffix)
    for path in (coarse_path, hero_path):
        if not path.is_dir():
            raise EncodeStormcastError(
                f"{path} missing — encode_stormcast needs BOTH stores "
                f"({coarse_path.name} and {hero_path.name}); untar the archive whole"
            )
    return zarr.open(str(coarse_path), mode="r"), zarr.open(str(hero_path), mode="r")


def frame_times(coarse, hero) -> list[str]:
    """ISO instants: init + each hero lead_time. StormCast steps hourly."""
    init = np.asarray(coarse["time"])[0]
    leads = np.asarray(hero["lead_time"])
    return [
        np.datetime_as_string(init + lead.astype("timedelta64[ns]"), unit="s") + "Z"
        for lead in leads
    ]


def coarse_window(coarse, hero_lat: np.ndarray, hero_lon: np.ndarray):
    """Row/col selectors for the GFS cells covering the StormCast domain.

    Derived from the hero grid's own bounds rather than hardcoded: StormCast v1's
    window is fixed, but reading it from the data means a different crop (or
    StormCastCONUS later) needs no edit here. One cell of margin so the resampled
    coarse layer covers the fine rect's edges instead of leaving a transparent rim.
    """
    lat = np.asarray(coarse["lat"])
    lon_raw = np.asarray(coarse["lon"])
    # hero_lon arrives already wrapped to -180..180, so compare in that frame
    # while selecting against the store's own ordering.
    lon = ((lon_raw + 180.0) % 360.0) - 180.0
    south, north = float(hero_lat.min()), float(hero_lat.max())
    west, east = float(hero_lon.min()), float(hero_lon.max())
    m = COARSE_STEP_DEG
    rows = np.where((lat >= south - m) & (lat <= north + m))[0]
    cols = np.where((lon >= west - m) & (lon <= east + m))[0]
    if len(rows) < 8 or len(cols) < 8:
        raise EncodeStormcastError(
            f"coarse window is only {len(rows)}x{len(cols)} cells for a domain "
            f"{south:.2f}..{north:.2f}N {west:.2f}..{east:.2f}E — the coarse store "
            "is not the 0.25 deg global grid this assumes"
        )
    return rows, cols, lat[rows], lon[cols]


def encode_layers(
    zarr_path: pathlib.Path,
    out_dir: pathlib.Path,
    event_config: pathlib.Path,
    config: pathlib.Path = DEFAULT_CONFIG,
    lut_dir: pathlib.Path = DEFAULT_LUT_DIR,
    tiles_dir: pathlib.Path = basemap_mod.DEFAULT_TILES,
    coastline_path: pathlib.Path = basemap_mod.DEFAULT_COASTLINE,
    event_id: str | None = None,
    mrms_path: pathlib.Path | None = None,
    member_paths: list[pathlib.Path] = (),
    init_override: str | None = None,
    report_url: str | None = None,
    fss_path: pathlib.Path | None = None,
) -> None:
    t0 = time.perf_counter()
    out_dir = pathlib.Path(out_dir)
    cfg = yaml.safe_load(pathlib.Path(event_config).read_text(encoding="utf-8"))
    if init_override is not None:
        cfg["init"] = init_override
    if report_url is not None:
        cfg["report"] = report_url
    framing_note = str(cfg.get("framing_note") or "").strip()
    if framing_note:
        framing_note = " " + framing_note

    coarse, hero = open_stores(zarr_path)
    times = frame_times(coarse, hero)
    nframes = len(times)
    specs = load_ramps(config)

    # The initialisation time, first-class. The frames alone cannot carry it: they
    # are valid times, and nothing in them says which one the model started from
    # or that the rest are predictions. The UI needs it to say how old a live
    # forecast is, so it is asserted against frame 0 rather than trusted — a
    # manifest that misstates its own init would mislabel every lead time. Checked
    # here, before any encoding, so a stale --init costs a second and not a run.
    init_iso = datetime.fromisoformat(str(cfg["init"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    if init_iso != times[0]:
        raise EncodeStormcastError(
            f"config init {init_iso} is not the first frame {times[0]} — the run being encoded "
            "is not the run the config describes (a stale --init, or the wrong zarr)"
        )

    hero_lat = np.asarray(hero["lat"])
    hero_lon = to_180(np.asarray(hero["lon"]))
    if hero_lon.max() - hero_lon.min() > 180.0:
        raise EncodeStormcastError(
            "the hero domain straddles the antimeridian after wrapping; a single "
            "equirectangular rect cannot describe it"
        )
    fine_grid = regrid.target_from_bbox(hero_lat, hero_lon)
    print(
        f"fine target grid: {fine_grid.width}x{fine_grid.height} "
        f"rect [{fine_grid.west:.4f}, {fine_grid.south:.4f}, "
        f"{fine_grid.east:.4f}, {fine_grid.north:.4f}]  ({nframes} frames, hourly)"
    )
    fine_index = regrid.build_index(hero_lat, hero_lon, fine_grid)

    rows, cols, sub_lat, sub_lon = coarse_window(coarse, hero_lat, hero_lon)
    sub_lon = to_180(sub_lon)
    coarse_lat2d, coarse_lon2d = np.meshgrid(sub_lat, sub_lon, indexing="ij")
    print(f"coarse window: {len(rows)}x{len(cols)} GFS cells "
          f"({sub_lat.min():.2f}..{sub_lat.max():.2f}N, "
          f"{sub_lon.min():.2f}..{sub_lon.max():.2f}E)")

    native_grid = regrid.TargetGrid(
        west=float(sub_lon.min()) - COARSE_STEP_DEG / 2,
        south=float(sub_lat.min()) - COARSE_STEP_DEG / 2,
        east=float(sub_lon.max()) + COARSE_STEP_DEG / 2,
        north=float(sub_lat.max()) + COARSE_STEP_DEG / 2,
        width=len(cols),
        height=len(rows),
    )
    coarse_on_fine = regrid.build_index(coarse_lat2d, coarse_lon2d, fine_grid)
    coarse_native = regrid.build_index(coarse_lat2d, coarse_lon2d, native_grid)

    # ── Field readers ─────────────────────────────────────────────────────────
    def hero_field(name: str):
        arr = hero[f"hero_{name}"]
        return lambda i: np.asarray(arr[0, i])

    def hero_wind():
        u, v = hero["hero_u10m"], hero["hero_v10m"]
        return lambda i: np.hypot(np.asarray(u[0, i]), np.asarray(v[0, i]))

    def coarse_field(name: str):
        arr = coarse[name]
        return lambda i: np.asarray(arr[0, i])[np.ix_(rows, cols)]

    def coarse_wind():
        u, v = coarse["u10m"], coarse["v10m"]
        return lambda i: np.hypot(
            np.asarray(u[0, i])[np.ix_(rows, cols)],
            np.asarray(v[0, i])[np.ix_(rows, cols)],
        )

    # (layer_id, kind, variable, reader, index, label suffix, pair_with, native_km)
    plan = [
        ("wind10m-fine", "hero-fine", "wind10m", hero_wind(), fine_index,
         " — generated, 3 km", "wind10m-coarse", 3.0),
        ("wind10m-coarse", "hero-coarse", "wind10m", coarse_wind(), coarse_on_fine,
         " — 25 km global forecast, resampled", "wind10m-fine", 25.0),
        ("wind10m-coarse-native", "hero-coarse", "wind10m", coarse_wind(), coarse_native,
         " — 25 km global forecast, shown at native resolution", None, 25.0),
        ("refc-fine", "hero-fine", "refc", hero_field("refc"), fine_index,
         " — generated, 3 km (a 25 km model produces no reflectivity at all)", None, 3.0),
        ("t2m-fine", "hero-fine", "t2m", hero_field("t2m"), fine_index,
         " — generated, 3 km", None, 3.0),
        ("t2m-coarse", "hero-coarse", "t2m", coarse_field("t2m"), coarse_on_fine,
         " — 25 km global forecast, resampled", None, 25.0),
    ]

    # ── Observed radar (optional): MRMS composite reflectivity on the same grid ──
    # This turns the reveal for refc into forecast-vs-what-happened. MRMS is a
    # NOAA radar mosaic at 0.01 deg (~1 km): -999 is outside radar coverage and
    # stays transparent; -99 is coverage with no echo and is a real 0 dBZ. The
    # frame times must match the forecast's exactly, or the wipe compares
    # different instants and calls it a verification.
    if mrms_path is not None:
        m = np.load(mrms_path)
        m_times = [str(v) for v in m["valid"]]
        if m_times != times:
            raise EncodeStormcastError(
                f"MRMS frames do not match the forecast frames: {m_times[:2]}... vs {times[:2]}..."
            )
        m_lat2d, m_lon2d = np.meshgrid(m["lat"], to_180(m["lon"]), indexing="ij")
        mrms_index = regrid.build_index(m_lat2d, m_lon2d, fine_grid, max_dist_km=2.0)
        m_raw = m["refc_half_dbz"]

        def mrms_field(i: int) -> np.ndarray:
            a = m_raw[i].astype(np.float32) / 2.0
            a[a == -99.0] = 0.0
            a[a == -999.0] = np.nan
            return a

        plan = [
            (lid, kind, var, read, index, suffix, "refc-observed" if lid == "refc-fine" else pair, km)
            for lid, kind, var, read, index, suffix, pair, km in plan
        ] + [
            ("refc-observed", "hero-observed", "refc", mrms_field, mrms_index,
             " — MRMS radar composite, observed", "refc-fine", 1.0),
        ]
        print(f"observed layer: MRMS {m_raw.shape[1]}x{m_raw.shape[2]} at 0.01 deg -> {fine_grid.width}x{fine_grid.height}")

    # ── Ensemble probability (optional): agreement across StormCast members ────
    # Members share the initial condition and the conditioning; only the
    # diffusion sampler's seed differs. Each must sit on exactly the hero grid
    # and the hero frames, or the fraction would be taken across different
    # places and instants and called agreement.
    if member_paths:
        import zarr

        members = []
        for p in member_paths:
            m = zarr.open(str(p), mode="r")
            for key in ("lat", "lon", "time", "lead_time"):
                if not np.array_equal(np.asarray(m[key]), np.asarray(hero[key])):
                    raise EncodeStormcastError(f"{p}: {key} differs from the hero store's")
            members.append(m)
        if len(members) < 2:
            raise EncodeStormcastError("an ensemble probability needs at least two --member stores")
        seeds = [m.attrs.get("seed") for m in members]

        def prob_field(i: int) -> np.ndarray:
            return exceedance_probability(
                np.stack([np.asarray(m["hero_refc"][0, i]) for m in members])
            )

        # The wipe compares like with like (the viewer refuses a pair whose
        # variables differ, and rightly: one legend cannot describe two ramps). So
        # the observed side of the probability is the radar's own exceedance in
        # the same units: 100 where MRMS reached the threshold, 0 elsewhere, NaN
        # outside coverage. Forecast probability against what happened.
        pair_id = None
        if mrms_path is not None:
            def observed_exceedance(i: int) -> np.ndarray:
                a = mrms_field(i)
                return np.where(np.isnan(a), np.nan, (a >= PROB_THRESHOLD_DBZ) * 100.0).astype(np.float32)

            plan.append((
                "prob40-observed", "hero-observed", "prob40", observed_exceedance, mrms_index,
                f" — MRMS radar at or above {PROB_THRESHOLD_DBZ:.0f} dBZ, observed", "prob40-fine", 1.0,
            ))
            pair_id = "prob40-observed"

        plan.append((
            "prob40-fine", "hero-fine", "prob40", prob_field, fine_index,
            f" — {len(members)}-member StormCast ensemble, 3 km", pair_id, 3.0,
        ))
        print(f"ensemble layer: {len(members)} members (seeds {seeds}) -> "
              f"probability of >= {PROB_THRESHOLD_DBZ:.0f} dBZ"
              + (", paired with the observed exceedance" if pair_id else ""))

    prepare_out_dir(out_dir)

    luts: dict[str, tuple[np.ndarray, str, str]] = {}
    (out_dir / "luts").mkdir()
    for variable in sorted({p[2] for p in plan}):
        src = pathlib.Path(lut_dir) / specs[variable].lut_filename
        if not src.is_file():
            raise EncodeStormcastError(
                f"{src} missing — bake the committed LUTs first: python -m latentsky.ramps"
            )
        rel = f"luts/{specs[variable].lut_filename}"
        shutil.copyfile(src, out_dir / rel)
        lut, sha = load_lut(out_dir / rel)
        luts[variable] = (lut, sha, rel)

    records: list[LayerRecord] = []
    for layer_id, kind, variable, read, index, suffix, pair, native_km in plan:
        spec = specs[variable]
        lut, lut_sha, lut_rel = luts[variable]
        frames: list[str] = []
        total = 0
        for i in range(nframes):
            rel = f"layers/{layer_id}/{i:03d}.webp"
            field = index.apply(read(i))
            total += encode_frame(
                field, lut, spec.vmin, spec.vmax, out_dir / rel,
                valid=index.valid & np.isfinite(field),
            )
            frames.append(rel)
        records.append(
            make_layer_record(
                layer_id=layer_id, kind=kind, spec=spec, lut_sha256=lut_sha,
                lut_rel_path=lut_rel, rect=index.grid.rect, size=index.grid.size,
                frames=frames, label_suffix=suffix, pair_with=pair,
                native_km=native_km,
            )
        )
        w, h = index.grid.size
        print(f"  {layer_id:22s} {kind:11s} {w:4d}x{h:<4d} "
              f"{len(frames)} frames  {total:>9,} B  ({total / len(frames):,.0f} B/frame)")

    size = basemap_mod.bake(tiles_dir, out_dir / BASEMAP_REL, coastline_path=coastline_path)
    print(f"  basemap baked: {BASEMAP_REL}  {size:,} B")

    # Where this run stands in the scoring loop, as a STATE rather than a promise.
    # The publish-time temptation is to write "verification arrives tomorrow", which
    # is false the first morning a scoring pass fails. "pending" says only "not
    # scored yet" and stays true however long that lasts, and a run nothing will
    # ever score says nothing at all.
    verification = "scored" if cfg.get("report") else (cfg.get("verification") or None)
    if verification not in (None, "pending", "scored"):
        raise EncodeStormcastError(f"config verification must be pending/scored, got {verification!r}")

    # The headline figure travels from the scorer's own results file into the
    # manifest, so the number on the globe is the number in the report by
    # construction rather than by anyone remembering to update it.
    summary = None
    if fss_path is not None:
        results = json.loads(pathlib.Path(fss_path).read_text(encoding="utf-8"))
        summary = results.get("headline")
        if summary is None:
            raise EncodeStormcastError(f"{fss_path} carries no headline block — re-run verify_fss")
        if verification != "scored":
            raise EncodeStormcastError(
                "a results file was given but this run is not marked scored; a headline figure "
                "must never appear beside a run whose verification has not been published"
            )

    run = {
        "id": event_id or cfg.get("id") or pathlib.Path(event_config).stem,
        "kind": "forecast",
        "init": init_iso,
        **({"verification": verification} if verification else {}),
        **({"verificationSummary": summary} if summary else {}),
        "model": {
            "prognostic": "NVIDIA StormCast v1, 3 km convection-allowing over the "
                          "central US (Apache-2.0 checkpoint), initialised from HRRR",
            "downscaling": "none — StormCast is convection-allowing throughout; the "
                           "coarse layer is the 25 km GFS forecast it is conditioned on",
        },
        # framing_note is the event's own claim about what kind of run this is.
        # Absent, nothing is claimed: saying nothing is always honest, and a
        # default inherited from another event is not.
        "generatedNote": (
            GENERATED_NOTE
            + framing_note
            + (MRMS_NOTE if mrms_path is not None else "")
            + (ENSEMBLE_NOTE.format(n=len(member_paths)) if member_paths else "")
        ),
        **run_hints(cfg),
    }
    manifest = build_manifest(
        run, times, records, specs,
        basemap={"global": BASEMAP_REL, "globalRect": GLOBAL_GRID.rect},
    )
    manifest_path = write_manifest(manifest, out_dir)
    print(f"\nmanifest validated against schema and written: {manifest_path}")
    print(f"encode wall time: {time.perf_counter() - t0:.1f} s")
    budget.report(out_dir)


# What is true of EVERY StormCast run this encoder emits. Anything true of one
# event only — which outbreak it is, whether the outcome was already known —
# belongs in that event's own `framing_note`, NOT here. A sentence hardcoded in
# this constant is published verbatim about every future run, which is how a
# live forecast came to describe itself as "a hindcast of the 14-15 March 2025
# outbreak, run after the fact" (caught in review, 3 Sep 2026, before it shipped).
GENERATED_NOTE = (
    "The fine layer is generated by NVIDIA's StormCast, a 3 km convection-allowing "
    "AI model, from HRRR initial conditions and conditioned on a 25 km global "
    "forecast — one plausible realisation, not observed truth. Radar reflectivity "
    "has no coarse counterpart: a 25 km global model cannot represent thunderstorms "
    "at all."
)

MRMS_NOTE = (
    " The observed reflectivity beside it is NOAA's MRMS radar composite at the "
    "same instants, resampled onto the same grid: what actually happened, from "
    "radar, so the comparison is forecast against observation."
)

ENSEMBLE_NOTE = (
    " The probability layer counts how many of {n} StormCast members — the same "
    "initial condition, only the diffusion sampler's random seed changed — put "
    "composite reflectivity at or above 40 dBZ in each cell. Where they agree the "
    "model is confident; where they do not, it is guessing, and the map says so."
)


def prepare_out_dir(out_dir: pathlib.Path) -> None:
    """Replace what this tool owns; refuse anything it does not."""
    managed = ("layers", "luts", "basemap", "manifest.json")
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in managed:
        path = out_dir / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    strangers = sorted(p.name for p in out_dir.iterdir())
    if strangers:
        raise EncodeStormcastError(
            f"{out_dir} contains entries this tool does not manage: {strangers} — "
            "delete them (or move them out) so the emitted tree means what it claims."
        )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--zarr", type=pathlib.Path, required=True,
                    help="the COARSE store; the _hero sibling is found beside it")
    ap.add_argument("--out", type=pathlib.Path, required=True)
    ap.add_argument("--event-config", type=pathlib.Path, required=True)
    ap.add_argument("--config", type=pathlib.Path, default=DEFAULT_CONFIG)
    ap.add_argument("--luts", type=pathlib.Path, default=DEFAULT_LUT_DIR)
    ap.add_argument("--event-id", default=None)
    ap.add_argument("--mrms", type=pathlib.Path, default=None,
                    help="MRMS composite-reflectivity npz (from mrms_fetch) to ship as the "
                         "observed layer refc-fine is compared against")
    ap.add_argument("--member", type=pathlib.Path, action="append", default=[],
                    help="per-member hero store of an ensemble run (repeatable, >= 2); "
                         "adds the prob40-fine agreement layer")
    ap.add_argument("--tiles", type=pathlib.Path, default=basemap_mod.DEFAULT_TILES,
                    help="NaturalEarthII tile root for the baked basemap (default: the cesium "
                         "package under web/node_modules; the daily image carries its own copy)")
    ap.add_argument("--coastline", type=pathlib.Path, default=basemap_mod.DEFAULT_COASTLINE)
    ap.add_argument("--init", default=None,
                    help="override the event config's init (ISO); pairs with forecast_stormcast --init")
    ap.add_argument("--report-url", default=None,
                    help="override the config's report link (a daily run names its page per day)")
    ap.add_argument("--fss", type=pathlib.Path, default=None,
                    help="verify_fss results JSON; its headline figure is copied into the manifest "
                         "so the site can state one measured number without the viewer opening the report")
    args = ap.parse_args(argv)
    encode_layers(
        args.zarr, args.out, args.event_config,
        config=args.config, lut_dir=args.luts, event_id=args.event_id,
        mrms_path=args.mrms, member_paths=args.member,
        tiles_dir=args.tiles, coastline_path=args.coastline, init_override=args.init,
        report_url=args.report_url, fss_path=args.fss,
    )


if __name__ == "__main__":
    main()
