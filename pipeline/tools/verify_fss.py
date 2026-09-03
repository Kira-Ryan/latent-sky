"""Score a StormCast run against MRMS observed composite reflectivity.

    python tools/verify_fss.py --hero-zarr data/zarr/dixie_2025_pre_hero.zarr \
        --mrms data/zarr/mrms_dixie_2025_pre.npz \
        --manifest data/web/dixie/manifest.json \
        --out data/verification/dixie_2025_pre.fss.json

    python tools/verify_fss.py --hero-zarr ... --selftest   # maths checks, no MRMS

Ensemble runs: pass every member's hero store with --member (repeatable). The
first is scored as the deterministic forecast; all of them feed the ensemble
probability score and the spread.

The maths lives in latentsky.verify so the tests can reach it; this file is the
command line.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np
import zarr

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from latentsky import verify  # noqa: E402


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hero-zarr", type=pathlib.Path, required=True,
                    help="the <out>_hero.zarr store of the run scored as the forecast")
    ap.add_argument("--member", type=pathlib.Path, action="append", default=[],
                    help="additional member hero stores (ensemble scoring); repeatable")
    ap.add_argument("--mrms", type=pathlib.Path, help="npz from tools/fetch_mrms.py")
    ap.add_argument("--manifest", type=pathlib.Path, default=None,
                    help="shipped manifest; asserts the scoring grid equals the display grid")
    ap.add_argument("--out", type=pathlib.Path, default=None)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--event-id", default=None, help="catalogue id of the run (goes into results.event)")
    ap.add_argument("--narrative", default="generic", choices=["generic", "dixie-2025"],
                    help="which prose the report renders: the Dixie case study, or the generic "
                         "data-driven reading a daily run gets")
    ap.add_argument("--live-url", default=None, help="where the run is on the site")
    args = ap.parse_args(argv)

    hero = zarr.open(str(args.hero_zarr), mode="r")
    grid, hlat, hlon = verify.display_grid(hero, args.manifest)
    fc = verify.forecast_on_grid(hero, hlat, hlon, grid)
    print(f"grid {grid.width}x{grid.height}; forecast {fc.shape}, footprint {np.isfinite(fc[0]).mean()*100:.1f}% of rect")

    if args.selftest:
        v = np.isfinite(fc[min(10, fc.shape[0] - 1)])
        a, b = fc[min(10, fc.shape[0] - 1)], fc[min(9, fc.shape[0] - 1)]
        same = verify.fss(a, a, v, 40.0, 11)
        zero = verify.fss(a, np.zeros_like(a), v, 40.0, 11)
        scale = [verify.fss(a, b, v, 40.0, w) for w in (1, 5, 11, 21, 41)]
        print(f"FSS(f,f)={same:.4f} FSS(f,0)={zero:.4f} by window: " + ", ".join(f"{s:.3f}" for s in scale))
        assert abs(same - 1.0) < 1e-9 and abs(zero) < 1e-9
        assert all(y >= x - 1e-9 for x, y in zip(scale, scale[1:]))
        print("SELFTEST PASSED")
        return

    if args.mrms is None or args.out is None:
        ap.error("--mrms and --out are required unless --selftest")

    obs, obs_times, offsets = verify.mrms_on_grid(args.mrms, grid)
    hero_times = [str(t) for t in np.asarray(hero["time"]).astype("datetime64[s]")]
    leads = np.asarray(hero["lead_time"]).astype("timedelta64[ns]")
    init = np.asarray(hero["time"])[0]
    fc_times = [np.datetime_as_string(init + lead, unit="s") + "Z" for lead in leads]
    if fc_times != obs_times:
        raise SystemExit(f"MRMS frames do not match forecast frames: {obs_times[:2]} vs {fc_times[:2]}")
    print(f"mrms {obs.shape}; radar coverage {np.isfinite(obs[0]).mean()*100:.1f}% of rect; offsets <= {np.abs(offsets).max():.0f}s")

    # The deterministic run is NOT a member: the members are the --member stores
    # only, so the probability field is what forecast_stormcast.py --members
    # produced and nothing else. The single run is scored alongside for contrast.
    members, member_seeds = None, []
    if args.member:
        stacks = []
        for p in args.member:
            h = zarr.open(str(p), mode="r")
            if not np.array_equal(np.asarray(h["lead_time"]).astype("timedelta64[ns]"), leads):
                raise SystemExit(f"{p}: lead_time axis differs from the scored run's")
            stacks.append(verify.forecast_on_grid(h, hlat, hlon, grid))
            member_seeds.append(h.attrs.get("seed"))
        members = np.stack(stacks)
        print(f"ensemble: {members.shape[0]} members, seeds {member_seeds}")

    results = verify.score(fc, obs, fc_times, grid, members=members)
    results["member_seeds"] = member_seeds
    results["single_run_seed"] = hero.attrs.get("seed")
    from datetime import datetime, timezone
    results["event"] = {
        "id": args.event_id,
        "narrative": args.narrative,
        "init": fc_times[0],
        "nsteps": len(fc_times) - 1,
        "live_url": args.live_url or (f"https://latent-sky.dev/?event={args.event_id}" if args.event_id else None),
        "scored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mrms_worst_offset_s": float(np.abs(offsets).max()),
    }
    if members is not None:
        # Member 0 was run with the single run's seed in a separate execution; how
        # far apart they are is a reproducibility fact worth reporting, not assuming.
        both = np.isfinite(fc) & np.isfinite(members[0])
        results["member0_vs_single_max_abs_diff"] = float(np.abs(members[0][both] - fc[both]).max())
        print(f"member 0 vs single run: max |diff| = {results['member0_vs_single_max_abs_diff']:.3f} dBZ")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")

    km = results["windows_km"]
    print(f"\n{'lead':>4} {'valid':>6} | {'obs>40':>7} {'fc>40':>7} | FSS@40 " + " ".join(f"{k:>6.0f}km" for k in km) + " | useful | sep")
    for r in results["leads"]:
        c, s = r["coverage"]["40"], r["fss"]["40"]
        sep = r["centroid_40dbz"]["separation_km"]
        print(f"{r['lead_h']:>3}h {r['valid'][11:16]:>6} | {c['observed']*100:6.2f}% {c['forecast']*100:6.2f}% | "
              + " ".join(f"{x:8.3f}" for x in s["by_window"]) + f" | {s['fss_useful']:6.3f} | {'-' if sep is None else round(sep):>4}")
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
