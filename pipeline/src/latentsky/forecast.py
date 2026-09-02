"""Latent Sky forecast stage — the one script the rented GPU runs.

Mirrors earth2studio's shipped `examples/03_downscaling/03_ensemble_downscaling.py`
(SFNO -> map_coords -> CorrDiffTaiwan, hand-rolled loop — deliberately NOT
run.diagnostic, whose generic path is not exercised against CorrDiff's sample/batch
dims). Config-driven so Doksuri / Gaemi / Koinu are one flag apart.

Run (inside the container, on a GPU instance):
    uv run python forecast.py --config configs/event_gaemi_2024.yaml

Deliberately self-contained: no imports from the latentsky encode package, so the
container needs only this file plus earth2studio. Everything downstream of the Zarr
this writes is a laptop job (Architecture.md §4.1).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

import yaml

# torch/numpy/earth2studio imports are deferred into the functions that need them,
# so --help and config validation work on machines without the GPU stack.


def log(msg: str) -> None:
    print(f"[{datetime.utcnow().isoformat(timespec='seconds')}Z] {msg}", flush=True)


def select_variables(x, coords, names: list[str]):
    """Slice a (…, variable, lat, lon) tensor down to the named variables.

    Dim order is looked up from coords rather than assumed — the iterator's leading
    dims differ between step 0 and later steps in some earth2studio versions.
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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--dry-run", action="store_true",
                    help="load config + models, run NO steps. The $2 VRAM probe.")
    # Overrides for shaking out the IO path in ~2 min instead of ~40 on a rented
    # GPU: --nsteps 1 --hero-steps 1 exercises every write path the full run uses.
    ap.add_argument("--nsteps", type=int, default=None,
                    help="override config nsteps (smoke tests)")
    ap.add_argument("--hero-steps", type=int, default=None,
                    help="override config hero_steps (smoke tests)")
    ap.add_argument("--ensemble-step", type=int, default=None,
                    help="override config ensemble_step, so a 1-step smoke still "
                         "exercises the ensemble write path")
    ap.add_argument("--output", default=None, help="override config output path")
    args = ap.parse_args()

    with open(args.config) as fh:
        cfg = yaml.safe_load(fh)

    init = datetime.fromisoformat(cfg["init"])
    nsteps: int = args.nsteps if args.nsteps is not None else cfg["nsteps"]
    hero_steps: int = args.hero_steps if args.hero_steps is not None else cfg["hero_steps"]
    ensemble_step: int = (
        args.ensemble_step if args.ensemble_step is not None
        else cfg.get("ensemble_step", -1)
    )
    ensemble_samples: int = cfg.get("ensemble_samples", 4)
    coarse_vars: list[str] = cfg["coarse_variables"]
    out_path: str = args.output if args.output is not None else cfg["output"]
    seed: int = cfg.get("seed", 0)

    log(f"config {args.config}: init={init.isoformat()} nsteps={nsteps} "
        f"hero_steps={hero_steps} -> {out_path}")

    # Imports deferred so `--help` works without the heavy stack.
    from collections import OrderedDict

    import numpy as np
    import torch
    import zarr
    from earth2studio.data import ARCO, fetch_data
    from earth2studio.io import ZarrBackend
    from earth2studio.models.dx import CorrDiffTaiwan
    from earth2studio.models.px import SFNO
    from earth2studio.utils.coords import map_coords, split_coords

    torch.manual_seed(seed)
    np.random.seed(seed)

    t0 = time.time()
    log("loading SFNO package (cached in image if BAKE_MODELS=1)…")
    sfno = SFNO.load_model(SFNO.load_default_package())
    log(f"SFNO loaded in {time.time() - t0:.0f}s")

    t0 = time.time()
    log("loading CorrDiffTaiwan package…")
    corrdiff = CorrDiffTaiwan.load_model(CorrDiffTaiwan.load_default_package())
    log(f"CorrDiff loaded in {time.time() - t0:.0f}s")

    device = torch.device(args.device)
    sfno = sfno.to(device)
    corrdiff = corrdiff.to(device)
    corrdiff.number_of_samples = 1

    if args.dry_run:
        free, total = torch.cuda.mem_get_info(device)
        log(f"DRY RUN OK — models resident. VRAM: {(total - free) / 2**30:.1f} GiB used "
            f"of {total / 2**30:.1f} GiB")
        return

    data = ARCO()  # anonymous GCS; init must be >= 3 months old (§3.6)

    # §3.5 — ZarrBackend defaults to NO compressor. Never omit zarr_codecs.
    # zarr 3 parses this as `compressors`, which must be a SEQUENCE of v3 codecs:
    # a bare numcodecs.Blosc (the v2 API) fails with "'Blosc' object is not
    # iterable" inside _parse_chunk_encoding_v3. Measured on the pod, 27 Aug 2026.
    # TWO stores, not one. Zarr keys coordinate arrays by name at the group root,
    # so a single store can hold exactly one "lat". The coarse field is 1-D
    # lat/lon (721/1440); CorrDiff's is a 2-D curvilinear 446x446 grid. Sharing a
    # store means the second registration collides with the first and every hero
    # write dies on "multidimension coordinates are passed in full" — the shapes
    # compared are the fine grid's against the coarse grid's. Measured on the pod,
    # 27 Aug 2026.
    def _store(path: str) -> ZarrBackend:
        return ZarrBackend(
            file_name=path,
            chunks={"sample": 1, "time": 1, "lead_time": 1},
            zarr_codecs=[
                zarr.codecs.BloscCodec(
                    cname="zstd", clevel=5, shuffle=zarr.codecs.BloscShuffle.bitshuffle
                )
            ],
        )

    hero_path = (
        out_path[: -len(".zarr")] + "_hero.zarr"
        if out_path.endswith(".zarr")
        else out_path + "_hero"
    )
    io = _store(out_path)
    io_hero = _store(hero_path)

    times = np.array([init], dtype="datetime64[ns]")
    sfno_ic = sfno.input_coords()

    log("fetching initial conditions from ARCO…")
    x, coords = fetch_data(
        source=data,
        time=times,
        variable=sfno_ic["variable"],
        lead_time=sfno_ic["lead_time"],
        device=device,
    )
    log(f"initial conditions {tuple(x.shape)} fetched")

    # ── Pre-register every output array, earth2studio's own pattern ────────────
    # io.write() alone cannot grow an axis: it indexes into an existing array, so
    # an array auto-created from step 0's coords has a length-1 lead_time and
    # every later step has nowhere to land. NVIDIA's 03_ensemble_downscaling.py
    # calls add_array up front with the FULL lead_time for exactly this reason.
    step_lead = sfno.output_coords(sfno_ic)["lead_time"]
    lead_all = np.asarray([step_lead * i for i in range(nsteps + 1)]).flatten()

    coarse_coords = OrderedDict(sfno.output_coords(sfno_ic))
    coarse_coords.pop("batch", None)
    coarse_coords["time"] = times
    coarse_coords["lead_time"] = lead_all
    coarse_coords.move_to_end("lead_time", last=False)
    coarse_coords.move_to_end("time", last=False)
    coarse_coords.pop("variable")
    io.add_array(coarse_coords, coarse_vars)

    # The hero arrays MUST NOT reuse the coarse variable names. CorrDiffTaiwan
    # outputs ["mrr","t2m","u10m","v10m"] — three of which collide with
    # coarse_variables — on a 446x446 curvilinear grid. Writing both under one
    # name means the second write indexes a global 721x1440 array. Prefix them.
    # The honesty panel shares the hero arrays rather than getting its own: a
    # ZarrBackend keeps ONE coords dict for the whole store, so two arrays with a
    # same-named axis of different lengths (sample=1 vs sample=4) cannot coexist —
    # the write indexes against the store's coords, not the array's. Registering
    # the full sample axis once and chunking per sample costs nothing on disk:
    # zarr never materialises a chunk that was not written, so the steps that
    # produce one sample occupy exactly one sample's worth of storage.
    ensemble_on = 0 <= ensemble_step < hero_steps and ensemble_samples > 1
    max_samples = ensemble_samples if ensemble_on else 1

    corrdiff.number_of_samples = max_samples
    fine_coords = OrderedDict(corrdiff.output_coords(corrdiff.input_coords()))
    fine_coords.pop("batch", None)
    fine_coords["time"] = times
    fine_coords["lead_time"] = lead_all[:hero_steps]
    fine_coords.move_to_end("lead_time", last=False)
    fine_coords.move_to_end("time", last=False)
    fine_vars = [str(v) for v in fine_coords.pop("variable")]
    io_hero.add_array(fine_coords, [f"hero_{v}" for v in fine_vars])
    corrdiff.number_of_samples = 1

    log(f"arrays registered: {len(coarse_vars)} coarse {tuple(coarse_coords['lat'].shape)} "
        f"-> {out_path}; {len(fine_vars)} hero x{max_samples} sample(s) -> {hero_path}")

    iterator = sfno.create_iterator(x, coords)

    for step, (xs, cs) in enumerate(iterator):
        if step > nsteps:
            break
        t_step = time.time()

        # Coarse global subset — every step. Writing all 73 variables would 10x the
        # archive for fields nothing renders (§5.2 lever 4).
        xc, cc = select_variables(xs, cs, coarse_vars)
        io.write(*split_coords(xc, cc))

        # Hero downscale — first hero_steps steps only. One CorrDiff call per
        # step: `sample` is 1 everywhere except the honesty-panel step, and both
        # land in the same arrays along the sample axis.
        if step < hero_steps:
            corrdiff.number_of_samples = (
                ensemble_samples if (ensemble_on and step == ensemble_step) else 1
            )
            xi, ci = map_coords(xs, cs, corrdiff.input_coords())
            xf, cf = corrdiff(xi, ci)
            xf_list, cf_out, fine_names = split_coords(xf, cf)
            io_hero.write(xf_list, cf_out, [f"hero_{n}" for n in fine_names])

        log(f"step {step:02d}/{nsteps} done in {time.time() - t_step:.1f}s "
            f"(samples={corrdiff.number_of_samples if step < hero_steps else 0})")

    log(f"forecast complete -> {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Errors must surface loudly, and the deadman still fires either way.
        import traceback
        traceback.print_exc()
        sys.exit(1)
