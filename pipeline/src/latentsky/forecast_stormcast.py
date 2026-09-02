"""Latent Sky StormCast stage — the CONUS convection-allowing run.

Sibling of forecast.py. That script runs SFNO -> CorrDiffTaiwan (a global
prognostic feeding a regional diffusion downscaler); this one runs NVIDIA's
StormCast v1, a single 3 km convection-allowing prognostic over the central US,
conditioned on a coarse global forecast at every step.

Everything hard-won in forecast.py carries over and is repeated here on purpose,
because each of these cost a failed run on 27 Aug 2026:

  * arrays are PRE-REGISTERED with their full lead_time axis. io.write() indexes
    into an existing array and cannot grow one, so an array auto-created from
    step 0 has a length-1 lead_time and every later step has nowhere to land.
  * zarr_codecs is a SEQUENCE of zarr-3 codecs. A bare numcodecs.Blosc (the v2
    API) dies inside _parse_chunk_encoding_v3 with "'Blosc' object is not iterable".
  * the coarse and fine fields go in SEPARATE stores. Zarr keys coordinate arrays
    by name at the group root, so one store holds exactly one "lat".

Three things are specific to StormCast and are NOT in forecast.py:

  1. TIME STEP IS 1 HOUR, not 6.
  2. StormCast.__call__ MUTATES ITS INPUT IN PLACE — `x[i, j, k:k+1] = self._forward(...)`
     with no clone (StormCastCONUS does clone; v1 does not; reported upstream as
     NVIDIA/earth2studio#1133). Every iterator is therefore created from its own
     clone of the initial condition, or the copy we hold is silently overwritten
     mid-run.
  3. The conditioning global forecast is fetched INSIDE every model step, from
     the data source handed to load_model(). That puts ~26 byte-range GETs in the
     inner loop, so per-step wall clock is part network latency, not just compute.

Output, deliberately shaped for latentsky.encode_forecast:
    <out>.zarr        coarse global GFS   u10m v10m t2m tcwv msl   [1, N, 721, 1440]
    <out>_hero.zarr   StormCast 3 km      hero_{u10m,v10m,t2m,msl,refc}
                                          [1, N, 512, 640] + 2-D lat/lon

With --members N the run is an ensemble: member k reseeds torch with seed+k,
integrates from its own copy of the same HRRR initial condition, and writes
<out>_m{k:02d}_hero.zarr (root attrs carry `member` and `seed`). The coarse store
is written once and the conditioning is shared. StormCast's spread comes from the
diffusion sampler's noise alone, so a member whose first step is identical to
member 0's means the seed never reached the sampler, and the run stops there.

StormCast's output_coords carry only hrrr_y/hrrr_x (projection metres), so the
2-D lat/lon the encoder needs for regridding are written explicitly from
model.lat / model.lon — CorrDiff supplied those in its coords for free.

Run (inside the container, on a GPU instance):
    uv run python forecast_stormcast.py --config configs/event_dixie_2025.yaml
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import yaml

# torch/numpy/earth2studio imports are deferred into main() so --help and config
# validation work on machines without the GPU stack.


def log(msg: str) -> None:
    print(f"[{datetime.utcnow().isoformat(timespec='seconds')}Z] {msg}", flush=True)


def select_variables(x, coords, names: list[str]):
    """Slice a (…, variable, …) tensor down to the named variables.

    Dim order is looked up from coords rather than assumed.
    """
    from collections import OrderedDict

    import numpy as np
    import torch

    dim = list(coords).index("variable")
    have = list(coords["variable"])
    missing = [n for n in names if n not in have]
    if missing:
        raise KeyError(f"variables not in model output: {missing} (have {len(have)})")
    idx = torch.tensor([have.index(n) for n in names], device=x.device)
    out = torch.index_select(x, dim, idx)
    new_coords = OrderedDict(coords)
    new_coords["variable"] = np.array(names)
    return out, new_coords


def hero_path_for(out_path: str, member: int | None) -> str:
    """<out>_hero.zarr for a single run; <out>_m{k:02d}_hero.zarr for ensemble member k."""
    stem = out_path[: -len(".zarr")] if out_path.endswith(".zarr") else out_path
    tag = "" if member is None else f"_m{member:02d}"
    return f"{stem}{tag}_hero.zarr"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--dry-run", action="store_true",
                    help="load config + model, run NO steps. The VRAM probe.")
    ap.add_argument("--nsteps", type=int, default=None,
                    help="override config nsteps (smoke tests)")
    ap.add_argument("--output", default=None, help="override config output path")
    ap.add_argument("--members", type=int, default=None,
                    help="ensemble size; overrides config `members` (default 1). Member k "
                         "reseeds with seed+k and writes <out>_m{k:02d}_hero.zarr")
    args = ap.parse_args()

    with open(args.config) as fh:
        cfg = yaml.safe_load(fh)

    init = datetime.fromisoformat(cfg["init"])
    nsteps: int = args.nsteps if args.nsteps is not None else cfg["nsteps"]
    coarse_vars: list[str] = cfg["coarse_variables"]
    hero_vars: list[str] = cfg["hero_variables"]
    out_path: str = args.output if args.output is not None else cfg["output"]
    seed: int = cfg.get("seed", 0)
    members: int = args.members if args.members is not None else int(cfg.get("members", 1))
    if members < 1:
        raise SystemExit(f"members must be >= 1, got {members}")

    log(f"config {args.config}: init={init.isoformat()} nsteps={nsteps} (hourly) "
        f"members={members} seed={seed} -> {out_path}")

    from collections import OrderedDict

    import numpy as np
    import torch
    import zarr
    from earth2studio.data import GFS_FX, HRRR, fetch_data
    from earth2studio.io import ZarrBackend
    from earth2studio.models.px import StormCast
    from earth2studio.utils.coords import map_coords, split_coords

    torch.manual_seed(seed)
    np.random.seed(seed)

    device = torch.device(args.device)

    # The conditioning source is a constructor argument, not a per-call one: the
    # model fetches from it inside every step.
    #
    # GFS_FX, not GFS. The plain GFS class is an ANALYSIS source — its __call__
    # ends in `xr_array.isel(lead_time=0)` and it only ever serves lead_time 0, so
    # StormCast's very first hourly step would ask it for +1 h and get nothing.
    # GFS_FX is the forecast source: 6-hourly cycles, hourly predictions out to
    # 384 h, back to Feb 2021 (verified against the 0.17.0 source). It is also the
    # honest coarse side of the reveal — a real 25 km forecast from the SAME init
    # and the SAME lead time as the 3 km field it is compared against.
    t0 = time.time()
    log("loading StormCast package (cached in image if BAKE_MODELS=1)…")
    conditioning = GFS_FX()
    model = StormCast.load_model(
        StormCast.load_default_package(), conditioning_data_source=conditioning
    )
    log(f"StormCast loaded in {time.time() - t0:.0f}s")

    model = model.to(device)

    # The variable list is NOT the module's VARIABLES constant — load_model reads
    # `metadata["variable"].values` out of the checkpoint, so the authoritative
    # names live in the weights. Resolve against the model before registering any
    # array, and say plainly what was asked for and what exists.
    available = [str(v) for v in model.variables]
    missing = [v for v in hero_vars if v not in available]
    if missing:
        log(f"WARNING: requested hero variables {missing} are NOT produced by this "
            f"checkpoint and will be DROPPED")
        log(f"the checkpoint's {len(available)} variables are: {available}")
        hero_vars = [v for v in hero_vars if v in available]
    if not hero_vars:
        raise SystemExit(
            f"FATAL: none of the requested hero variables exist in this checkpoint. "
            f"Available: {available}"
        )
    log(f"hero variables resolved: {hero_vars}")

    if args.dry_run:
        free, total = torch.cuda.mem_get_info(device)
        log(f"DRY RUN OK — model resident. VRAM: {(total - free) / 2**30:.1f} GiB used "
            f"of {total / 2**30:.1f} GiB")
        log(f"domain: lat {float(model.lat.min()):.2f}..{float(model.lat.max()):.2f}, "
            f"lon {float(model.lon.min()):.2f}..{float(model.lon.max()):.2f}, "
            f"grid {model.lat.shape}")
        return

    times = np.array([init], dtype="datetime64[ns]")
    ic = model.input_coords()

    # ── Initial conditions: HRRR analysis, cropped to StormCast's window ───────
    # HRRR serves the full 1059x1799 CONUS grid; map_coords selects the
    # hrrr_y/hrrr_x subset the model declares, which IS the 512x640 crop.
    log("fetching HRRR initial conditions…")
    x, coords = fetch_data(
        source=HRRR(),
        time=times,
        variable=ic["variable"],
        lead_time=ic["lead_time"],
        device=device,
    )
    log(f"HRRR fetched {tuple(x.shape)}; cropping to the StormCast window")
    x, coords = map_coords(x, coords, ic)
    log(f"initial conditions {tuple(x.shape)}")

    # earth2studio's HRRR source SILENTLY leaves a channel as zeros when a variable
    # is missing from the GRIB index (hrrr.py logs and continues). That is a
    # poisoned forecast with no error raised, so check it here rather than trust it.
    flat = x.reshape(x.shape[-3], -1) if x.ndim >= 3 else x
    dead = [
        str(v) for i, v in enumerate(coords["variable"])
        if float(torch.abs(flat[i]).max()) == 0.0
    ]
    if dead:
        raise SystemExit(
            f"FATAL: {len(dead)} HRRR channels are identically zero: {dead[:12]}"
            f"{' …' if len(dead) > 12 else ''}. earth2studio skips variables missing "
            "from the GRIB index and leaves the preallocated zeros, which would run "
            "a forecast on fabricated input. Pick another init or product."
        )
    log(f"all {len(coords['variable'])} input channels carry data")

    # ── Stores ────────────────────────────────────────────────────────────────
    def _store(path: str) -> ZarrBackend:
        return ZarrBackend(
            file_name=path,
            chunks={"time": 1, "lead_time": 1},
            zarr_codecs=[
                zarr.codecs.BloscCodec(
                    cname="zstd", clevel=5, shuffle=zarr.codecs.BloscShuffle.bitshuffle
                )
            ],
        )

    io_coarse = _store(out_path)
    hero_paths = (
        [hero_path_for(out_path, None)]
        if members == 1
        else [hero_path_for(out_path, k) for k in range(members)]
    )

    step_lead = np.timedelta64(1, "h")
    lead_all = np.asarray([step_lead * i for i in range(nsteps + 1)]).flatten()

    # Coarse GFS store, on the global 0.25 deg grid the encoder's taiwan_subset()
    # style gating expects. Fetched once for the whole window rather than per step.
    log("fetching GFS conditioning for the coarse store…")
    gfs_x, gfs_coords = fetch_data(
        source=conditioning,
        time=times,
        variable=np.array(coarse_vars),
        lead_time=lead_all,
        device=device,
    )
    log(f"GFS fetched {tuple(gfs_x.shape)}")
    coarse_coords = OrderedDict()
    coarse_coords["time"] = times
    coarse_coords["lead_time"] = lead_all
    coarse_coords["lat"] = gfs_coords["lat"]
    coarse_coords["lon"] = gfs_coords["lon"]
    io_coarse.add_array(coarse_coords, coarse_vars)
    gx, gc, gnames = split_coords(gfs_x, gfs_coords)
    io_coarse.write(gx, gc, gnames)
    log(f"coarse store written: {len(coarse_vars)} variables "
        f"{tuple(gfs_coords['lat'].shape)}x{tuple(gfs_coords['lon'].shape)}")

    # The 2-D geolocation the hero grid needs, written into every hero store.
    lat2d = model.lat.cpu().numpy() if hasattr(model.lat, "cpu") else np.asarray(model.lat)
    lon2d = model.lon.cpu().numpy() if hasattr(model.lon, "cpu") else np.asarray(model.lon)
    log(f"hero geolocation: lat/lon {lat2d.shape} "
        f"({lat2d.min():.2f}..{lat2d.max():.2f} N, {lon2d.min():.2f}..{lon2d.max():.2f} E)")

    def open_hero(path: str, member: int, member_seed: int) -> ZarrBackend:
        """A hero store on StormCast's own index grid, every array pre-registered.

        hrrr_y/hrrr_x are projection metres, not degrees — the 2-D lat/lon written
        alongside are what the encoder regrids from.
        """
        io = _store(path)
        hero_coords = OrderedDict()
        hero_coords["time"] = times
        hero_coords["lead_time"] = lead_all
        hero_coords["hrrr_y"] = model.hrrr_y
        hero_coords["hrrr_x"] = model.hrrr_x
        io.add_array(hero_coords, [f"hero_{v}" for v in hero_vars])
        for name, arr in (("lat", lat2d), ("lon", lon2d)):
            io.root.create_array(
                name, shape=arr.shape, chunks=arr.shape, dtype="float32",
                dimension_names=["hrrr_y", "hrrr_x"],
            )
            io.root[name][:] = arr.astype("float32")
        io.root.attrs.update({"member": member, "seed": member_seed, "members": members})
        return io

    # ── Integrate ─────────────────────────────────────────────────────────────
    # StormCast v1 mutates its input in place (NVIDIA/earth2studio#1133), so every
    # member integrates from its own copy of the untouched initial condition. The
    # only source of spread is the diffusion sampler's noise, which is why each
    # member reseeds torch: a first step identical to member 0's means the seed
    # never reached the sampler, and stopping there beats writing N copies of one
    # forecast.
    first_step_ref = None
    for k, hero_path in enumerate(hero_paths):
        member_seed = seed + k
        torch.manual_seed(member_seed)
        np.random.seed(member_seed)
        io_hero = open_hero(hero_path, k, member_seed)
        t_member = time.time()
        iterator = model.create_iterator(x.clone(), coords)

        for step, (xs, cs) in enumerate(iterator):
            if step > nsteps:
                break
            t_step = time.time()
            xh, ch = select_variables(xs, cs, hero_vars)
            xl, cl, names = split_coords(xh, ch)
            io_hero.write(xl, cl, [f"hero_{n}" for n in names])
            peak = float(xs.max())
            log(f"member {k} step {step:02d}/{nsteps} done in {time.time() - t_step:.1f}s "
                f"(max value {peak:.1f})")
            if step == 1:
                if first_step_ref is None:
                    first_step_ref = xh.detach().clone()
                else:
                    spread = float((xh - first_step_ref).abs().max())
                    log(f"member {k} vs member 0 at step 1: max |diff| = {spread:.3f}")
                    if spread == 0.0:
                        raise SystemExit(
                            f"FATAL: member {k} (seed {member_seed}) reproduced member 0 "
                            "exactly at step 1 — the seed is not reaching the sampler"
                        )

        log(f"member {k} (seed {member_seed}) complete in {time.time() - t_member:.0f}s "
            f"-> {hero_path}")

    log(f"forecast complete -> {out_path} + {len(hero_paths)} hero store(s)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Errors must surface loudly, and the deadman still fires either way.
        import traceback
        traceback.print_exc()
        sys.exit(1)
