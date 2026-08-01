"""§7.2(b) gate 2: any frame encoded with non-global vmin/vmax must fail the build.

The coarse wind field peaks at 23.6 m/s against a global vmax of 55 — autoscaling
it to "use the full ramp" is precisely the failure that erases the finding the
hero exists to show. It must be impossible to commit, not merely discouraged.
"""

from __future__ import annotations

import numpy as np
import pytest

from latentsky import encode, manifest


def _record(spec, lut_sha, layer_id, vmin, vmax):
    return encode.LayerRecord(
        layer_id=layer_id,
        kind="hero-coarse",
        variable=spec.variable,
        label=spec.label,
        units=spec.units,
        rect=[116.0, 19.0, 126.0, 28.0],
        size=[8, 8],
        lut=f"luts/{spec.lut_filename}",
        vmin=vmin,
        vmax=vmax,
        identity=encode.identity_checksum(spec.variable, lut_sha, vmin, vmax, spec.alpha),
        frames=["frame.webp"],
    )


def test_global_range_passes(specs, lut_dir):
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    encode.verify_global_ranges([_record(spec, sha, "ok", spec.vmin, spec.vmax)], specs)


@pytest.mark.parametrize(
    "vmin,vmax",
    [
        (0.0, 23.6),   # autoscaled to the coarse field's own max — the classic failure
        (2.0, 55.0),   # trimmed floor
        (0.0, 52.24),  # autoscaled to the fine field's own max
    ],
)
def test_non_global_range_fails(specs, lut_dir, vmin, vmax):
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    with pytest.raises(encode.GlobalRangeError, match="GLOBAL"):
        encode.verify_global_ranges([_record(spec, sha, "bad", vmin, vmax)], specs)


def test_unknown_variable_fails(specs, lut_dir):
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    record = _record(spec, sha, "bad", spec.vmin, spec.vmax)
    record.variable = "gust"  # not in ramps.yaml
    with pytest.raises(encode.GlobalRangeError, match="gust"):
        encode.verify_global_ranges([record], specs)


def test_manifest_build_runs_the_range_gate(specs, lut_dir):
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    bad = _record(spec, sha, "wind10m-coarse", 0.0, 23.6)
    run = {
        "id": "test",
        "kind": "dev-sample",
        "model": {"prognostic": "x", "downscaling": "y"},
        "generatedNote": "test",
    }
    with pytest.raises(encode.GlobalRangeError):
        manifest.build_manifest(run, ["2021-01-01T00:00:00Z"], [bad], specs)


def test_quantise_identical_arithmetic_both_sides(specs):
    """The same physical value must land on the same LUT index from either layer."""
    spec = specs["wind10m"]
    coarse_field = np.array([[10.0, 23.6]])
    fine_field = np.array([[10.0, 23.6]])
    ic, _ = encode.quantise(coarse_field, spec.vmin, spec.vmax)
    if_, _ = encode.quantise(fine_field, spec.vmin, spec.vmax)
    assert np.array_equal(ic, if_)
