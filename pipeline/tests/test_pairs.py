"""A reveal pair must compare one variable; the build refuses anything else.

The viewer enforces this at load time in the browser (manifest.ts), which is
the wrong place to find out. build_manifest mirrors the rule so the encoder
fails on the machine that ran it.
"""

from __future__ import annotations

import pytest

from latentsky import encode, manifest

RUN = {
    "id": "test",
    "kind": "forecast",
    "model": {"prognostic": "x", "downscaling": "y"},
    "generatedNote": "test",
}
TIMES = ["2025-03-14T18:00:00Z"]


def _record(spec, lut_sha, layer_id, kind, pair_with=None):
    return encode.LayerRecord(
        layer_id=layer_id,
        kind=kind,
        variable=spec.variable,
        label=spec.label,
        units=spec.units,
        rect=[-109.6, 31.1, -85.4, 45.4],
        size=[8, 8],
        lut=f"luts/{spec.lut_filename}",
        vmin=spec.vmin,
        vmax=spec.vmax,
        identity=encode.identity_checksum(spec.variable, lut_sha, spec.vmin, spec.vmax, spec.alpha),
        frames=["a.webp"],
        pair_with=pair_with,
    )


def test_pair_across_variables_is_refused(specs, lut_dir):
    _, sha_p = encode.load_lut(lut_dir / specs["prob40"].lut_filename)
    _, sha_r = encode.load_lut(lut_dir / specs["refc"].lut_filename)
    prob = _record(specs["prob40"], sha_p, "prob40-fine", "hero-fine", pair_with="refc-observed")
    radar = _record(specs["refc"], sha_r, "refc-observed", "hero-observed")
    with pytest.raises(manifest.ManifestError, match="same variable"):
        manifest.build_manifest(RUN, TIMES, [prob, radar], specs)


def test_pair_within_a_variable_builds(specs, lut_dir):
    _, sha = encode.load_lut(lut_dir / specs["prob40"].lut_filename)
    prob = _record(specs["prob40"], sha, "prob40-fine", "hero-fine", pair_with="prob40-observed")
    obs = _record(specs["prob40"], sha, "prob40-observed", "hero-observed", pair_with="prob40-fine")
    m = manifest.build_manifest(RUN, TIMES, [prob, obs], specs)
    assert m["layers"]["prob40-fine"]["pairWith"] == "prob40-observed"


def test_dangling_pair_is_refused(specs, lut_dir):
    _, sha = encode.load_lut(lut_dir / specs["prob40"].lut_filename)
    prob = _record(specs["prob40"], sha, "prob40-fine", "hero-fine", pair_with="nowhere")
    with pytest.raises(manifest.ManifestError, match="not a layer id"):
        manifest.build_manifest(RUN, TIMES, [prob], specs)
