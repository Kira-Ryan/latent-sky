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
    args = ap.parse_args()

    with open(args.config) as fh:
        cfg = yaml.safe_load(fh)

    init = datetime.fromisoformat(cfg["init"])
    nsteps: int = cfg["nsteps"]
    hero_steps: int = cfg["hero_steps"]
    ensemble_step: int = cfg.get("ensemble_step", -1)
    ensemble_samples: int = cfg.get("ensemble_samples", 4)
    coarse_vars: list[str] = cfg["coarse_variables"]
    out_path: str = cfg["output"]
    seed: int = cfg.get("seed", 0)

    log(f"config {args.config}: init={init.isoformat()} nsteps={nsteps} "
        f"hero_steps={hero_steps} -> {out_path}")

    # Imports deferred so `--help` works without the heavy stack.
    import numcodecs
    import numpy as np
    import torch
    from earth2studio.data import ARCO
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
    io = ZarrBackend(
        file_name=out_path,
        zarr_codecs=numcodecs.Blosc(cname="zstd", clevel=5,
                                    shuffle=numcodecs.Blosc.BITSHUFFLE),
    )

    log("creating SFNO iterator (fetches initial conditions from ARCO)…")
    iterator = sfno.create_iterator(*sfno.input_coords(), data, [init])

    for step, (x, coords) in enumerate(iterator):
        if step > nsteps:
            break
        t_step = time.time()

        # Coarse global subset — every step. Writing all 73 variables would 10x the
        # archive for fields nothing renders (§5.2 lever 4).
        xc, cc = select_variables(x, coords, coarse_vars)
        io.write(*split_coords(xc, cc))

        # Hero downscale — first hero_steps steps only.
        if step < hero_steps:
            corrdiff.number_of_samples = (
                ensemble_samples if step == ensemble_step else 1
            )
            xi, ci = map_coords(x, coords, corrdiff.input_coords())
            xf, cf = corrdiff(xi, ci)
            io.write(*split_coords(xf, cf))

        log(f"step {step:02d}/{nsteps} done in {time.time() - t_step:.1f}s "
            f"(samples={corrdiff.number_of_samples})")

    log(f"forecast complete -> {out_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Errors must surface loudly, and the deadman still fires either way.
        import traceback
        traceback.print_exc()
        sys.exit(1)
