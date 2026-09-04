"""Round-trip proof for the WebP path — the §5.5 exact-alpha question, settled.

RESULT on the installed stack (Pillow 12.3.0): `lossless=True, exact=True`
round-trips BIT-IDENTICALLY including RGB under alpha=0, and omitting `exact`
demonstrably corrupts those pixels. No alternative encoding (index-in-RGB with
a separate alpha channel policy) is needed; encode.save_webp additionally
re-decodes every frame it writes, so a future Pillow/libwebp regression fails
the build rather than shipping corrupt ramp tails.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from latentsky import encode, ramps


def _adversarial_rgba() -> np.ndarray:
    """Every byte value in every channel, plus non-zero RGB under alpha=0 —
    exactly the pixels libwebp's default alpha cleanup rewrites."""
    rng = np.random.default_rng(42)
    rgba = rng.integers(0, 256, size=(64, 256, 4), dtype=np.uint8)
    rgba[:16, :, 3] = 0                      # transparent band with random (nonzero) RGB
    rgba[16, :, :3] = np.arange(256)[:, None]  # full ramp sweep
    rgba[16, :, 3] = 0                       # ...fully transparent
    rgba[17, :, 3] = 1                       # ...and nearly transparent
    return rgba


def test_webp_round_trip_bit_identical(tmp_path):
    rgba = _adversarial_rgba()
    out = tmp_path / "adversarial.webp"
    encode.save_webp(rgba, out)
    back = np.asarray(Image.open(out).convert("RGBA"), dtype=np.uint8)
    assert np.array_equal(rgba, back), "exact-alpha WebP round-trip must be bit-identical"


def test_webp_default_would_corrupt(tmp_path):
    """Documents WHY exact=True is mandatory. If this ever fails, libwebp's default
    became exact-preserving — harmless, but §5.5's rationale should be revisited."""
    rgba = _adversarial_rgba()
    out = tmp_path / "default.webp"
    Image.fromarray(rgba, mode="RGBA").save(out, format="WEBP", lossless=True, method=6)
    back = np.asarray(Image.open(out).convert("RGBA"), dtype=np.uint8)
    assert not np.array_equal(rgba, back)
    # ...and the damage is confined to RGB under alpha=0; visible pixels survive.
    visible = rgba[..., 3] != 0
    assert np.array_equal(rgba[visible], back[visible])


def test_full_encode_path_round_trip(tmp_path, specs, lut_dir):
    """field -> quantise -> LUT -> WebP file -> decode == in-memory RGBA, bit for bit."""
    spec = specs["wind10m"]
    lut, _ = encode.load_lut(lut_dir / spec.lut_filename)
    rng = np.random.default_rng(7)
    field = rng.uniform(-5.0, 60.0, size=(48, 40))       # deliberately overshoots the range
    field[0, :4] = np.nan                                 # out-of-footprint pixels
    rgba = encode.colourise(field, lut, spec.vmin, spec.vmax)
    out = tmp_path / "frame.webp"
    encode.encode_frame(field, lut, spec.vmin, spec.vmax, out)
    back = np.asarray(Image.open(out).convert("RGBA"), dtype=np.uint8)
    assert np.array_equal(rgba, back)
    assert (back[0, :4] == 0).all(), "NaN pixels must be fully transparent (0,0,0,0)"


def test_quantise_is_the_spec_arithmetic():
    """§5.5: idx = uint8(clip((f - vmin)/(vmax - vmin), 0, 1) * 255 + 0.5)."""
    field = np.array([[-1.0, 0.0, 27.5, 55.0, 999.0]])
    idx, finite = encode.quantise(field, 0.0, 55.0)
    assert idx.tolist() == [[0, 0, 128, 255, 255]]
    assert finite.all()


def test_lut_shape_and_alpha_policy(specs, lut_dir):
    """The baked wind LUT: alpha 0 at values <= 2 m/s, 255 from 6 m/s, monotonic between."""
    spec = specs["wind10m"]
    lut, sha = encode.load_lut(lut_dir / spec.lut_filename)
    assert lut.shape == (256, 4) and len(sha) == 64
    values = spec.vmin + np.arange(256) * (spec.vmax - spec.vmin) / 255.0
    assert (lut[values <= 2.0, 3] == 0).all()
    assert (lut[values >= 6.0, 3] == 255).all()
    assert (np.diff(lut[:, 3].astype(int)) >= 0).all()
    # t2m is opaque everywhere
    t2m_lut, _ = encode.load_lut(lut_dir / specs["t2m"].lut_filename)
    assert (t2m_lut[:, 3] == 255).all()


def test_save_webp_rejects_bad_input(tmp_path):
    with pytest.raises(ValueError):
        encode.save_webp(np.zeros((4, 4, 3), dtype=np.uint8), tmp_path / "x.webp")
    with pytest.raises(ValueError):
        encode.save_webp(np.zeros((4, 4, 4), dtype=np.float32), tmp_path / "x.webp")


def test_lossless_webp_uses_maximum_compression_effort():
    """`quality` on lossless WebP is libwebp's EFFORT knob, and Pillow defaults it
    to 80. The default costs ~26% of every frame's bytes for identical pixels, so
    the setting is pinned here: a refactor that drops it silently inflates the
    whole site's payload and nothing else would notice."""
    import inspect
    src = inspect.getsource(encode.save_webp)
    assert "quality=100" in src, "save_webp lost its compression-effort setting"
    assert "exact=True" in src, "save_webp lost exact-alpha, which corrupts transparent pixels"


def test_effort_setting_actually_shrinks_frames_without_changing_them(tmp_path):
    import io
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(7)
    # Structured, not pure noise: noise is incompressible and would hide the effect.
    base = np.zeros((256, 256, 4), dtype=np.uint8)
    yy, xx = np.mgrid[0:256, 0:256]
    base[..., 0] = (yy // 8 * 8).astype(np.uint8)
    base[..., 1] = (xx // 8 * 8).astype(np.uint8)
    base[..., 2] = ((xx + yy) // 16 * 16).astype(np.uint8)
    base[..., 3] = np.where(rng.random((256, 256)) > 0.3, 255, 0).astype(np.uint8)

    def encoded(**kw):
        buf = io.BytesIO()
        Image.fromarray(base, "RGBA").save(buf, format="WEBP", lossless=True, exact=True, method=6, **kw)
        return buf.getvalue()

    default, effort = encoded(), encoded(quality=100)
    assert len(effort) < len(default), "quality=100 did not reduce the frame"
    for blob in (default, effort):
        back = np.asarray(Image.open(io.BytesIO(blob)).convert("RGBA"), dtype=np.uint8)
        assert np.array_equal(base, back), "a lossless encode changed the pixels"
