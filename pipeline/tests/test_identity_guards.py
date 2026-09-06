"""A run may only be encoded and scored with its own stores and its own results.

Three failures an adversarial review reproduced on 6 Sep 2026, each of which
produced a schema-valid manifest: a hero store from another day paired with
today's coarse store; an ensemble member from another day or another grid
accepted by the scorer and sampled with the scored run's coordinates; a results
file from another run attached as this run's verification, complete with its
headline. The daily pipeline cannot produce any of them by itself, because one
process writes every store and one tar carries them, so these guard the hand-run
and mis-untarred paths. They cost nothing and they close the one false public
claim this site must never make.
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pytest
import zarr
from PIL import Image

from latentsky import encode_stormcast as es

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import verify_fss  # noqa: E402

SEP = "2026-09-05T12:00:00"
SEP_ISO = SEP + "Z"
MAR = "2025-03-14T18:00:00"

CFG_TEXT = """\
init: "2026-09-05T12:00:00"
nsteps: 3
hero_variables: [u10m, v10m, t2m, refc]
coarse_variables: [u10m, v10m, t2m, tcwv, msl]
seed: 1200
output: /out/daily.zarr
storm_name: "Central US"
place_label: "Central US · StormCast 3 km domain"
hero_frame: 1
framing_note: >-
  This run was made from the most recent analysis available.
verification: pending
default_variable: refc
"""


def _put(g, name, arr):
    a = g.create_array(name, shape=arr.shape, dtype=arr.dtype)
    a[:] = arr


def write_hero(path, init_iso, leads_h, lat0=33.0, lon0=262.0, n=100, step=0.03, seed=0):
    g = zarr.open_group(str(path), mode="w")
    _put(g, "time", np.array([init_iso], dtype="datetime64[ns]"))
    _put(g, "lead_time", np.array([np.timedelta64(h, "h") for h in leads_h]).astype("timedelta64[ns]"))
    lat1 = lat0 + step * np.arange(n)
    lon1 = lon0 + step * np.arange(n)
    lon2d, lat2d = np.meshgrid(lon1, lat1)
    _put(g, "lat", lat2d.astype("float32"))
    _put(g, "lon", lon2d.astype("float32"))
    rng = np.random.default_rng(seed)
    for v, (lo, hi) in {"u10m": (-5, 5), "v10m": (-5, 5), "t2m": (280, 300), "refc": (0, 35)}.items():
        data = rng.uniform(lo, hi, size=(1, len(leads_h), n, n)).astype("float32")
        if v == "refc":
            data[0, :, 40:60, 40:60] = 50.0
        _put(g, f"hero_{v}", data)
    g.attrs.update({"member": 0, "seed": seed, "members": 1})
    return path


def write_coarse(path, init_iso, leads_h, seed=1):
    g = zarr.open_group(str(path), mode="w")
    _put(g, "time", np.array([init_iso], dtype="datetime64[ns]"))
    _put(g, "lead_time", np.array([np.timedelta64(h, "h") for h in leads_h]).astype("timedelta64[ns]"))
    lat = np.arange(50.0, 24.9, -0.25).astype("float32")
    lon = np.arange(245.0, 280.01, 0.25).astype("float32")
    _put(g, "lat", lat)
    _put(g, "lon", lon)
    rng = np.random.default_rng(seed)
    for v, (lo, hi) in {"u10m": (-5, 5), "v10m": (-5, 5), "t2m": (280, 300), "tcwv": (10, 40), "msl": (100000, 102000)}.items():
        _put(g, v, rng.uniform(lo, hi, size=(1, len(leads_h), len(lat), len(lon))).astype("float32"))
    return path


def write_mrms(path, valid_iso):
    n = len(valid_iso)
    lat = (36.5 - 0.01 * np.arange(401)).astype("float32")
    lon = (261.5 + 0.01 * np.arange(401)).astype("float32")
    raw = np.full((n, 401, 401), -99 * 2, dtype=np.int16)
    raw[:, 150:220, 150:220] = 50 * 2
    np.savez_compressed(path, refc_half_dbz=raw, lat=lat, lon=lon, valid=np.array(valid_iso),
                        offset_s=np.zeros(n), keys=np.array(["k"] * n))
    return path


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "event.yaml"
    p.write_text(CFG_TEXT, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def fake_tiles(monkeypatch):
    """No Natural Earth tiles in tests: the basemap bake writes a 4x2 placeholder."""
    def fake_bake(tiles_root, out_path, quality=90, coastline_path=None):
        out_path = pathlib.Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (4, 2)).save(out_path, format="WEBP")
        return out_path.stat().st_size
    monkeypatch.setattr(es.basemap_mod, "bake", fake_bake)


def encode(d, cfg, lut_dir, **kw):
    return es.encode_layers(d / "run.zarr", d / "site", cfg, lut_dir=lut_dir,
                            event_id="daily-2026-09-05", init_override=SEP, **kw)


def results_for(init_iso, nleads, members=None):
    return {"init": init_iso, "members": members, "leads": [{} for _ in range(nleads)],
            "event": {"id": "x", "init": init_iso},
            "headline": {"thresholdDbz": 40, "usefulScaleKm": 98.2, "usefulHours": 3,
                         "scoredHours": 3, "largestScaleKm": 98.2}}


# ── the encoder ───────────────────────────────────────────────────────────────

def test_a_matching_pair_encodes(tmp_path, cfg, lut_dir):
    write_coarse(tmp_path / "run.zarr", SEP, [0, 1, 2, 3])
    write_hero(tmp_path / "run_hero.zarr", SEP, [0, 1, 2, 3])
    encode(tmp_path, cfg, lut_dir)
    m = json.loads((tmp_path / "site" / "manifest.json").read_text(encoding="utf-8"))
    assert len(m["frames"]) == 4 and m["run"]["init"] == SEP_ISO


def test_a_hero_store_from_another_day_is_refused(tmp_path, cfg, lut_dir):
    """Coarse from 5 Sep 2026, hero from 14 Mar 2025: previously a manifest
    labelled 5 Sep whose fine layers were the March outbreak."""
    write_coarse(tmp_path / "run.zarr", SEP, [0, 1, 2, 3])
    write_hero(tmp_path / "run_hero.zarr", MAR, [0, 1, 2, 3])
    with pytest.raises(es.EncodeStormcastError, match="not the same forecast"):
        encode(tmp_path, cfg, lut_dir)
    assert not (tmp_path / "site" / "manifest.json").exists()


def test_a_mismatched_lead_axis_is_refused(tmp_path, cfg, lut_dir):
    """Coarse hourly, hero two-hourly: previously the coarse frame labelled 14Z
    held the 13Z field, because coarse frames are read by index."""
    write_coarse(tmp_path / "run.zarr", SEP, [0, 1, 2, 3, 4, 5, 6])
    write_hero(tmp_path / "run_hero.zarr", SEP, [0, 2, 4, 6])
    with pytest.raises(es.EncodeStormcastError, match="lead axis"):
        encode(tmp_path, cfg, lut_dir)


def test_a_results_file_from_another_run_is_refused(tmp_path, cfg, lut_dir):
    """A March report with 17 useful hours attached to a two-frame September
    run: previously published as scored, with the March headline on the globe."""
    write_coarse(tmp_path / "run.zarr", SEP, [0, 1])
    write_hero(tmp_path / "run_hero.zarr", SEP, [0, 1])
    fss = tmp_path / "march.fss.json"
    fss.write_text(json.dumps(results_for("2025-03-14T18:00:00Z", 19)), encoding="utf-8")
    with pytest.raises(es.EncodeStormcastError, match="different run"):
        encode(tmp_path, cfg, lut_dir, report_url="/verification/daily-2026-09-05.html", fss_path=fss)
    assert not (tmp_path / "site" / "manifest.json").exists()


def test_a_results_file_with_the_wrong_frame_count_is_refused(tmp_path, cfg, lut_dir):
    write_coarse(tmp_path / "run.zarr", SEP, [0, 1, 2, 3])
    write_hero(tmp_path / "run_hero.zarr", SEP, [0, 1, 2, 3])
    fss = tmp_path / "short.fss.json"
    fss.write_text(json.dumps(results_for(SEP_ISO, 2)), encoding="utf-8")
    with pytest.raises(es.EncodeStormcastError, match="different run"):
        encode(tmp_path, cfg, lut_dir, report_url="/verification/daily-2026-09-05.html", fss_path=fss)


def test_this_runs_own_results_are_accepted(tmp_path, cfg, lut_dir):
    write_coarse(tmp_path / "run.zarr", SEP, [0, 1, 2, 3])
    write_hero(tmp_path / "run_hero.zarr", SEP, [0, 1, 2, 3])
    fss = tmp_path / "own.fss.json"
    fss.write_text(json.dumps(results_for(SEP_ISO, 4)), encoding="utf-8")
    encode(tmp_path, cfg, lut_dir, report_url="/verification/daily-2026-09-05.html", fss_path=fss)
    m = json.loads((tmp_path / "site" / "manifest.json").read_text(encoding="utf-8"))
    assert m["run"]["verification"] == "scored"
    assert m["run"]["verificationSummary"]["usefulHours"] == 3


# ── the scorer ────────────────────────────────────────────────────────────────

def _score(tmp_path, members):
    hero = write_hero(tmp_path / "sep_hero.zarr", SEP, [0, 1, 2, 3], seed=0)
    mrms = write_mrms(tmp_path / "mrms.npz", [f"2026-09-05T{12 + h:02d}:00:00Z" for h in range(4)])
    out = tmp_path / "fss.json"
    args = ["--hero-zarr", str(hero), "--mrms", str(mrms), "--out", str(out), "--event-id", "daily-2026-09-05"]
    for m in members:
        args += ["--member", str(m)]
    verify_fss.main(args)
    return json.loads(out.read_text(encoding="utf-8"))


def test_the_scorer_refuses_a_member_from_another_day(tmp_path):
    """Previously accepted, and sampled with the scored run's coordinates."""
    mar = write_hero(tmp_path / "m01_hero.zarr", MAR, [0, 1, 2, 3], seed=1)
    ok = write_hero(tmp_path / "m02_hero.zarr", SEP, [0, 1, 2, 3], seed=2)
    with pytest.raises(SystemExit, match="time differs"):
        _score(tmp_path, [mar, ok])


def test_the_scorer_refuses_a_member_on_another_grid(tmp_path):
    """A member shifted ten degrees north had a 100% footprint on the scoring
    grid when read with the hero's coordinates, and 0% with its own."""
    shifted = write_hero(tmp_path / "m01_hero.zarr", SEP, [0, 1, 2, 3], lat0=43.0, seed=1)
    ok = write_hero(tmp_path / "m02_hero.zarr", SEP, [0, 1, 2, 3], seed=2)
    with pytest.raises(SystemExit, match="lat differs"):
        _score(tmp_path, [shifted, ok])


def test_the_scorer_refuses_duplicate_seeds(tmp_path):
    a = write_hero(tmp_path / "m01_hero.zarr", SEP, [0, 1, 2, 3], seed=7)
    b = write_hero(tmp_path / "m02_hero.zarr", SEP, [0, 1, 2, 3], seed=7)
    with pytest.raises(SystemExit, match="distinct"):
        _score(tmp_path, [a, b])


def test_the_scorer_accepts_matching_members(tmp_path):
    a = write_hero(tmp_path / "m01_hero.zarr", SEP, [0, 1, 2, 3], seed=1)
    b = write_hero(tmp_path / "m02_hero.zarr", SEP, [0, 1, 2, 3], seed=2)
    r = _score(tmp_path, [a, b])
    assert r["members"] == 2 and r["member_seeds"] == [1, 2] and r["init"] == SEP_ISO
