"""Basemap bake: TMS y-flip, dark-palette bounds, determinism, manifest wiring.

Uses SYNTHETIC tiles built in tmp dirs — never the Cesium node_modules tree, so
these tests run in CI where web/ dependencies may not be installed.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from latentsky import basemap, encode, manifest


def _make_tiles(root, cols=basemap.COLS, rows=basemap.ROWS):
    """A synthetic TMS zoom-2 tree: each tile a solid colour encoding (x, tms_y)."""
    zoom = root / str(basemap.ZOOM)
    for x in range(cols):
        for tms_y in range(rows):
            (zoom / str(x)).mkdir(parents=True, exist_ok=True)
            colour = (25 * x + 5, 60 * tms_y + 5, 128)
            tile = Image.new("RGB", (basemap.TILE_PX, basemap.TILE_PX), colour)
            tile.save(zoom / str(x) / f"{tms_y}.jpg", quality=95)
    return root


def test_stitch_flips_the_tms_y_axis(tmp_path):
    """TMS y=0 is the SOUTH row: it must land at the BOTTOM of the stitched image,
    and tms_y = rows-1 at the top. Solid-colour tiles make the answer known."""
    stitched = basemap.stitch(_make_tiles(tmp_path))
    assert stitched.shape == (basemap.HEIGHT, basemap.WIDTH, 3)
    top_left = stitched[:basemap.TILE_PX, :basemap.TILE_PX].reshape(-1, 3).mean(axis=0)
    bottom_left = stitched[-basemap.TILE_PX:, :basemap.TILE_PX].reshape(-1, 3).mean(axis=0)
    # G channel encodes tms_y: top row must be tms_y=3 (G~185), bottom tms_y=0 (G~5)
    assert abs(top_left[1] - (60 * 3 + 5)) < 8, f"top-left G {top_left[1]} != tms_y=3"
    assert abs(bottom_left[1] - 5) < 8, f"bottom-left G {bottom_left[1]} != tms_y=0"
    # x placement sanity: rightmost column block must be x=7 (R~180)
    top_right = stitched[:basemap.TILE_PX, -basemap.TILE_PX:].reshape(-1, 3).mean(axis=0)
    assert abs(top_right[0] - (25 * 7 + 5)) < 8


def test_stitch_fails_loudly_on_missing_tiles(tmp_path):
    root = _make_tiles(tmp_path)
    (root / str(basemap.ZOOM) / "3" / "1.jpg").unlink()
    with pytest.raises(basemap.BasemapError, match="tile missing"):
        basemap.stitch(root)
    with pytest.raises(basemap.BasemapError, match="zoom directory"):
        basemap.stitch(tmp_path / "nowhere")


def test_style_dark_is_bounded_by_the_palette():
    """Any input whatsoever must come out inside the palette's anchor box —
    nothing saturated can survive the restyle."""
    rng = np.random.default_rng(99)
    wild = rng.integers(0, 256, size=(64, 128, 3), dtype=np.uint8)
    out = basemap.style_dark(wild)
    anchors = np.stack([basemap._hex_rgb(c) for c in (
        basemap.OCEAN_DEEP, basemap.OCEAN_SHELF, basemap.LAND_DARK, basemap.LAND_BRIGHT)])
    lo, hi = anchors.min(axis=0), anchors.max(axis=0)
    for ch in range(3):
        assert out[..., ch].min() >= lo[ch] - 1
        assert out[..., ch].max() <= hi[ch] + 1
    with pytest.raises(ValueError):
        basemap.style_dark(wild.astype(np.float32))


def test_bake_is_deterministic(tmp_path):
    root = _make_tiles(tmp_path)
    a, b = tmp_path / "a.webp", tmp_path / "b.webp"
    size_a = basemap.bake(root, a)
    size_b = basemap.bake(root, b)
    assert size_a == size_b
    assert a.read_bytes() == b.read_bytes(), "two bakes of the same tiles must be byte-identical"
    img = Image.open(a)
    assert img.format == "WEBP" and img.size == (basemap.WIDTH, basemap.HEIGHT)


def _minimal_layer(specs, lut_dir, out_dir):
    """One schema-valid wind layer whose assets exist on disk under out_dir."""
    import shutil
    spec = specs["wind10m"]
    (out_dir / "luts").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(lut_dir / spec.lut_filename, out_dir / "luts" / spec.lut_filename)
    _, sha = encode.load_lut(out_dir / "luts" / spec.lut_filename)
    (out_dir / "f0.webp").write_bytes(b"x")  # existence-checked only
    return encode.LayerRecord(
        layer_id="wind10m-global", kind="global", variable=spec.variable,
        label=spec.label, units=spec.units, rect=[-180.0, -90.0, 180.0, 90.0],
        size=[720, 361], lut=f"luts/{spec.lut_filename}", vmin=spec.vmin, vmax=spec.vmax,
        identity=encode.identity_checksum(spec.variable, sha, spec.vmin, spec.vmax, spec.alpha),
        frames=["f0.webp"],
    )


def _run():
    return {"id": "t", "kind": "dev-sample",
            "model": {"prognostic": "x", "downscaling": "y"}, "generatedNote": "t"}


def test_manifest_carries_the_basemap_object(specs, lut_dir, tmp_path):
    record = _minimal_layer(specs, lut_dir, tmp_path)
    (tmp_path / "basemap").mkdir()
    (tmp_path / "basemap" / "global-dark.webp").write_bytes(b"x")
    built = manifest.build_manifest(
        _run(), ["2021-01-01T00:00:00Z"], [record], specs,
        basemap={"global": "basemap/global-dark.webp", "globalRect": [-180, -90, 180, 90]},
    )
    path = manifest.write_manifest(built, tmp_path)  # schema-validates, checks files
    import json
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["basemap"] == {
        "global": "basemap/global-dark.webp", "globalRect": [-180.0, -90.0, 180.0, 90.0]
    }


def test_manifest_missing_basemap_file_fails(specs, lut_dir, tmp_path):
    record = _minimal_layer(specs, lut_dir, tmp_path)
    built = manifest.build_manifest(
        _run(), ["2021-01-01T00:00:00Z"], [record], specs,
        basemap={"global": "basemap/global-dark.webp"},
    )
    with pytest.raises(manifest.ManifestError, match="basemap.global"):
        manifest.write_manifest(built, tmp_path)


def test_manifest_rejects_bad_basemap_objects(specs, lut_dir, tmp_path):
    record = _minimal_layer(specs, lut_dir, tmp_path)
    with pytest.raises(manifest.ManifestError, match="unknown keys"):
        manifest.build_manifest(_run(), ["2021-01-01T00:00:00Z"], [record], specs,
                                basemap={"globe": "x.webp"})
    with pytest.raises(manifest.ManifestError, match="no imagery"):
        manifest.build_manifest(_run(), ["2021-01-01T00:00:00Z"], [record], specs,
                                basemap={"globalRect": [-180, -90, 180, 90]})
    with pytest.raises(manifest.ManifestError, match="globalRect"):
        manifest.build_manifest(_run(), ["2021-01-01T00:00:00Z"], [record], specs,
                                basemap={"global": "x.webp", "globalRect": [1, 2, 3]})
