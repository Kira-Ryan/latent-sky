"""encode_public — the publishable pre-forecast subset and its licence gates.

Unit tests are synthetic (no network, no data/dev — absent in CI). The two
committed-tree tests at the bottom prove the shipped data/web artefact itself:
the manifest carries no hero layers anywhere, and (locally, where data/dev
exists) no file in data/web is byte-identical to any CC BY-NC-ND dev hero frame.
"""

from __future__ import annotations

import hashlib
import json
import pathlib

import numpy as np
import pytest
from PIL import Image

from latentsky import basemap as basemap_mod
from latentsky import encode_public
from latentsky.encode import LayerRecord

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA_WEB = REPO_ROOT / "data" / "web"
DEV_ENCODED = REPO_ROOT / "data" / "dev" / "encoded"
DEV_RAW_CWB = REPO_ROOT / "data" / "dev" / "raw" / "cwb_sample.npz"


def _record(layer_id: str, kind: str) -> LayerRecord:
    return LayerRecord(
        layer_id=layer_id, kind=kind, variable="wind10m", label="x", units="m/s",
        rect=[0.0, 0.0, 1.0, 1.0], size=[8, 8], lut="luts/wind10m.lut.png",
        vmin=0.0, vmax=55.0, identity="f" * 64, frames=["a.webp"],
    )


# ------------------------------------------------------------------ gate 1: kinds

def test_verify_publishable_accepts_global_only():
    encode_public.verify_publishable([_record("wind10m-global", "global")])


@pytest.mark.parametrize("kind", ["hero-fine", "hero-coarse"])
def test_verify_publishable_rejects_hero_kinds(kind):
    records = [_record("wind10m-global", "global"), _record("bad-layer", kind)]
    with pytest.raises(encode_public.PublicLicenceError, match="bad-layer"):
        encode_public.verify_publishable(records)


def test_publishable_kinds_is_global_only():
    """The allowlist itself is part of the licence posture — pin it."""
    assert encode_public.PUBLISHABLE_KINDS == frozenset({"global"})


# ------------------------------------------------------------------ gate 2: bytes

def test_verify_no_hero_bytes_skips_when_dev_encode_absent(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.webp").write_bytes(b"anything")
    assert encode_public.verify_no_hero_bytes(out, tmp_path / "no-such-dir") == 0


def _fake_dev_encode(tmp_path: pathlib.Path, frame_bytes: bytes) -> pathlib.Path:
    dev = tmp_path / "dev-encoded"
    (dev / "layers" / "wind10m-fine").mkdir(parents=True)
    (dev / "layers" / "wind10m-fine" / "000.webp").write_bytes(frame_bytes)
    manifest = {
        "layers": {
            "wind10m-fine": {
                "kind": "hero-fine",
                "frames": ["layers/wind10m-fine/000.webp"],
            },
            "wind10m-global": {  # non-hero layers must NOT enter the deny set
                "kind": "global",
                "frames": ["layers/wind10m-global/000.webp"],
            },
        }
    }
    (dev / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return dev


def test_verify_no_hero_bytes_detects_a_byte_identical_frame(tmp_path):
    dev = _fake_dev_encode(tmp_path, b"HERO-FRAME-BYTES")
    out = tmp_path / "out"
    (out / "layers" / "sneaky").mkdir(parents=True)
    (out / "layers" / "sneaky" / "000.webp").write_bytes(b"HERO-FRAME-BYTES")
    with pytest.raises(encode_public.PublicLicenceError, match="sneaky"):
        encode_public.verify_no_hero_bytes(out, dev)


def test_verify_no_hero_bytes_passes_on_distinct_bytes(tmp_path):
    dev = _fake_dev_encode(tmp_path, b"HERO-FRAME-BYTES")
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.webp").write_bytes(b"honest public bytes")
    assert encode_public.verify_no_hero_bytes(out, dev) == 1


def test_verify_no_hero_bytes_fails_loudly_on_a_broken_dev_tree(tmp_path):
    dev = _fake_dev_encode(tmp_path, b"x")
    (dev / "layers" / "wind10m-fine" / "000.webp").unlink()
    with pytest.raises(encode_public.EncodePublicError, match="missing hero frame"):
        encode_public.verify_no_hero_bytes(tmp_path, dev)


# ------------------------------------------------------------------ output directory

def test_prepare_out_dir_replaces_managed_entries(tmp_path):
    out = tmp_path / "web"
    (out / "layers" / "old").mkdir(parents=True)
    (out / "layers" / "old" / "000.webp").write_bytes(b"stale")
    (out / "manifest.json").write_text("{}", encoding="utf-8")
    encode_public.prepare_out_dir(out)
    assert list(out.iterdir()) == []


def test_prepare_out_dir_rejects_strangers(tmp_path):
    out = tmp_path / "web"
    out.mkdir()
    (out / "notes.txt").write_text("not ours", encoding="utf-8")
    with pytest.raises(encode_public.EncodePublicError, match="notes.txt"):
        encode_public.prepare_out_dir(out)


# ------------------------------------------------------------------ synthetic end-to-end

def _synthetic_raw(tmp_path: pathlib.Path, times: list[str] | None = None) -> pathlib.Path:
    """A synthetic era5_gaemi_week.npz on the exact half-degree layout
    fetch_era5 --half-degree caches (0.5°, lat 90..-90, lon 0..359.5)."""
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    lat = 90.0 - 0.5 * np.arange(361)
    lon = 0.5 * np.arange(720)
    if times is None:
        times = list(encode_public.CACHE_TIMES)
    latg = np.radians(lat)[:, None]
    long_ = np.radians(lon)[None, :]
    frames = np.arange(len(times), dtype=np.float64)[:, None, None]
    u10m = 8.0 * np.sin(3.0 * long_ + frames) * np.cos(latg)
    v10m = 6.0 * np.cos(2.0 * long_ - frames) * np.cos(latg)
    t2m = 288.0 - 60.0 * np.sin(latg) ** 2 + 2.0 * frames + 3.0 * np.sin(long_)
    tcwv = 30.0 + 25.0 * np.cos(latg) * np.sin(long_ + frames)
    msl = 101325.0 + 1500.0 * np.sin(2.0 * long_ + frames) * np.cos(latg)  # Pa, like ARCO
    np.savez(
        raw / encode_public.RAW_NAME,
        latitude=lat, longitude=lon, times=np.array(times),
        u10m=u10m.astype(np.float32), v10m=v10m.astype(np.float32),
        t2m=t2m.astype(np.float32), tcwv=tcwv.astype(np.float32),
        msl=msl.astype(np.float32),
    )
    return raw


def _synthetic_tiles(tmp_path: pathlib.Path) -> pathlib.Path:
    """A zoom-2 NE2-shaped TMS tile tree (8x4 of 256 px) with ocean and land pixels."""
    tiles = tmp_path / "tiles"
    grad = np.linspace(0, 255, 256).astype(np.uint8)
    ocean = np.zeros((256, 256, 3), dtype=np.uint8)
    ocean[..., 2] = 150 + (grad[None, :] // 4)      # strongly blue
    ocean[..., 1] = 60
    land = np.zeros((256, 256, 3), dtype=np.uint8)  # green/tan — lands on the land side
    land[..., 0] = 120
    land[..., 1] = 130 + (grad[:, None] // 4)
    land[..., 2] = 60
    for x in range(basemap_mod.COLS):
        for y in range(basemap_mod.ROWS):
            d = tiles / str(basemap_mod.ZOOM) / str(x)
            d.mkdir(parents=True, exist_ok=True)
            tile = ocean if (x + y) % 2 == 0 else land
            Image.fromarray(tile, mode="RGB").save(d / f"{y}.jpg", format="JPEG", quality=85)
    return tiles


@pytest.fixture(scope="module")
def public_tree(tmp_path_factory, lut_dir):
    """Run the real encode twice into the same directory; expose both tree hashes."""
    tmp = tmp_path_factory.mktemp("public")
    raw = _synthetic_raw(tmp)
    tiles = _synthetic_tiles(tmp)
    out = tmp / "web"
    kwargs = dict(lut_dir=lut_dir, tiles_dir=tiles,
                  dev_encoded_dir=tmp / "no-dev-encode")
    first = encode_public.encode_layers(raw, out, **kwargs)
    second = encode_public.encode_layers(raw, out, **kwargs)
    return out, first, second


def test_rerun_reproduces_an_identical_tree(public_tree):
    out, first, second = public_tree
    assert first == second, "re-running encode_public must reproduce the tree byte-for-byte"


def test_manifest_is_the_public_preforecast_contract(public_tree):
    out, _, _ = public_tree
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    run = manifest["run"]
    assert run["kind"] == "dev-sample"
    assert "Copernicus Climate Change Service information 2024" in run["generatedNote"]
    assert "Typhoon Gaemi" in run["generatedNote"]
    assert "not a forecast" in run["generatedNote"]
    assert "hero layer arrives with the first forecast run" in run["generatedNote"]
    # Hero-experience hints must be absent pre-forecast. Gaemi is context in the
    # note, never a stormName — a storm-chase affordance needs a hero layer.
    for key in ("stormName", "heroFrame", "placeLabel", "init"):
        assert key not in run, f"run.{key} has no meaning without a hero layer"

    # The REAL sequence: 16 12-hourly frames across the Gaemi week, in order.
    assert manifest["frames"] == list(encode_public.PUBLIC_TIMES)
    assert len(manifest["frames"]) == 16
    assert manifest["frames"][0] == "2024-07-22T00:00:00Z"
    assert manifest["frames"][-1] == "2024-07-29T12:00:00Z"

    assert sorted(manifest["layers"]) == ["msl-global", "t2m-global", "tcwv-global", "wind10m-global"]
    for layer_id, entry in manifest["layers"].items():
        assert entry["kind"] == "global", f"{layer_id}: NO hero layers may ship pre-forecast"
        assert entry["size"] == [720, 361]
        assert entry["rect"] == [-180.0, -90.0, 180.0, 90.0]
        assert len(entry["frames"]) == 16
        for rel in [entry["lut"], *entry["frames"]]:
            assert (out / rel).is_file(), f"{layer_id}: manifest references missing {rel}"
    assert (out / manifest["basemap"]["global"]).is_file()


def test_public_t2m_range_override_flows_from_the_manifest(public_tree, specs, lut_dir):
    """The −70 °C floor ships in manifest vmin (where the Legend reads it), the
    LUT bytes stay identical to the committed bake, and the other variables
    keep their ramps.yaml ranges untouched."""
    out, _, _ = public_tree
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

    t2m = manifest["layers"]["t2m-global"]
    assert t2m["vmin"] == encode_public.T2M_PUBLIC_VMIN == 203.15
    assert t2m["vmax"] == specs["t2m"].vmax == 323.15
    assert t2m["label"].startswith("2 m temperature"), "the label stays honest"
    # LUT reuse: byte-identical to the ramps.yaml bake — colours only, no range.
    vendored = (out / t2m["lut"]).read_bytes()
    assert vendored == (lut_dir / "t2m.lut.png").read_bytes()

    assert manifest["layers"]["wind10m-global"]["vmin"] == specs["wind10m"].vmin
    assert manifest["layers"]["wind10m-global"]["vmax"] == specs["wind10m"].vmax
    assert manifest["layers"]["tcwv-global"]["vmax"] == specs["tcwv"].vmax


def test_public_specs_guards_its_own_preconditions(specs):
    import dataclasses
    overridden = encode_public.public_specs(specs)
    assert overridden["t2m"].vmin == 203.15
    assert specs["t2m"].vmin == 233.15, "the loaded specs must never be mutated"
    assert overridden["wind10m"] is specs["wind10m"]

    # A ramp alpha policy bakes the range into the LUT — the override must refuse.
    ramped = dict(specs)
    ramped["t2m"] = dataclasses.replace(
        specs["t2m"], alpha={"policy": "ramp", "zeroBelow": 240.0, "oneBy": 250.0}
    )
    with pytest.raises(encode_public.EncodePublicError, match="opaque"):
        encode_public.public_specs(ramped)


def test_emitted_tree_contains_only_managed_entries(public_tree):
    out, _, _ = public_tree
    assert sorted(p.name for p in out.iterdir()) == sorted(encode_public.MANAGED_ENTRIES)


def test_sequence_gate_rejects_a_wrong_cache(tmp_path, lut_dir):
    # Not the Gaemi week at all
    raw = _synthetic_raw(tmp_path, times=[f"2021-0{m}-02T00:00:00Z" for m in range(1, 6)])
    with pytest.raises(encode_public.EncodePublicError, match="Gaemi-week sequence"):
        encode_public.encode_layers(raw, tmp_path / "web", lut_dir=lut_dir)


def test_sequence_gate_rejects_a_truncated_cache(tmp_path, lut_dir):
    # The right week but missing the final step — the caption would lie.
    raw = _synthetic_raw(tmp_path, times=list(encode_public.CACHE_TIMES[:-1]))
    with pytest.raises(encode_public.EncodePublicError, match="Gaemi-week sequence"):
        encode_public.encode_layers(raw, tmp_path / "web", lut_dir=lut_dir)


def test_cache_and_public_time_constants_cohere():
    assert len(encode_public.CACHE_TIMES) == 32
    assert len(encode_public.PUBLIC_TIMES) == 16
    assert encode_public.PUBLIC_TIMES == encode_public.CACHE_TIMES[::2]
    assert all(t[11:13] in ("00", "12") for t in encode_public.PUBLIC_TIMES)


# ------------------------------------------------------------------ the committed tree

def test_committed_data_web_manifest_has_no_hero_layers():
    """Runs wherever data/web exists (including CI): the SHIPPED manifest itself."""
    manifest_path = DATA_WEB / "manifest.json"
    if not manifest_path.is_file():
        pytest.skip("data/web not yet encoded — run: python -m latentsky.encode_public")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["run"]["kind"] == "dev-sample"
    for key in ("stormName", "heroFrame", "placeLabel"):
        assert key not in manifest["run"]
    for layer_id, entry in manifest["layers"].items():
        assert entry["kind"] == "global", (
            f"committed data/web declares hero layer {layer_id!r} — "
            "hero layers derive from CC BY-NC-ND data and must not ship pre-forecast"
        )


def test_committed_data_web_has_no_dev_hero_bytes():
    """PROOF for the licence gate: zero files in data/web are byte-identical to any
    data/dev hero frame (or to the raw CWB sample). Local-only — data/dev is
    gitignored and absent in CI, where the kind gates above still run."""
    if not DATA_WEB.is_dir():
        pytest.skip("data/web not yet encoded — run: python -m latentsky.encode_public")
    deny = encode_public.dev_hero_frame_hashes(DEV_ENCODED)
    if not deny:
        pytest.skip("data/dev/encoded absent (CI) — byte-identity proof runs locally")
    if DEV_RAW_CWB.is_file():
        deny[hashlib.sha256(DEV_RAW_CWB.read_bytes()).hexdigest()] = "raw: cwb_sample.npz"

    collisions = []
    for path in sorted(p for p in DATA_WEB.rglob("*") if p.is_file()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in deny:
            collisions.append(f"{path.relative_to(DATA_WEB).as_posix()} == {deny[digest]}")
    assert not collisions, (
        "data/web contains files byte-identical to CC BY-NC-ND dev data:\n  "
        + "\n  ".join(collisions)
    )
    assert len(deny) >= 25, "deny set implausibly small — dev hero frames not found?"
