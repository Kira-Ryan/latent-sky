"""§7.2(b) gate 1: coarse and fine layers of one variable must share one identity."""

from __future__ import annotations

import numpy as np
import pytest

from latentsky import encode, manifest


def _record(spec, lut_sha, layer_id, kind, frames, vmin=None, vmax=None, identity=None):
    vmin = spec.vmin if vmin is None else vmin
    vmax = spec.vmax if vmax is None else vmax
    return encode.LayerRecord(
        layer_id=layer_id,
        kind=kind,
        variable=spec.variable,
        label=spec.label,
        units=spec.units,
        rect=[116.0, 19.0, 126.0, 28.0],
        size=[8, 8],
        lut=f"luts/{spec.lut_filename}",
        vmin=vmin,
        vmax=vmax,
        identity=identity
        or encode.identity_checksum(spec.variable, lut_sha, vmin, vmax, spec.alpha),
        frames=frames,
    )


def test_coarse_and_fine_identities_equal(specs, lut_dir):
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    fine = _record(spec, sha, "wind10m-fine", "hero-fine", ["a.webp"])
    coarse = _record(spec, sha, "wind10m-coarse", "hero-coarse", ["b.webp"])
    assert fine.identity == coarse.identity
    encode.verify_identity([fine, coarse])  # must not raise


def test_identity_is_sensitive_to_every_input(specs, lut_dir):
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    base = encode.identity_checksum(spec.variable, sha, spec.vmin, spec.vmax, spec.alpha)
    assert encode.identity_checksum("t2m", sha, spec.vmin, spec.vmax, spec.alpha) != base
    assert encode.identity_checksum(spec.variable, "0" * 64, spec.vmin, spec.vmax, spec.alpha) != base
    assert encode.identity_checksum(spec.variable, sha, 1.0, spec.vmax, spec.alpha) != base
    assert encode.identity_checksum(spec.variable, sha, spec.vmin, 40.0, spec.alpha) != base
    assert (
        encode.identity_checksum(spec.variable, sha, spec.vmin, spec.vmax, {"policy": "opaque"})
        != base
    )


def test_deliberate_mismatch_fails(specs, lut_dir):
    """A coarse layer quietly autoscaled to its own max — the classic failure — must
    be refused by the identity gate, loudly, before any manifest can exist."""
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    fine = _record(spec, sha, "wind10m-fine", "hero-fine", ["a.webp"])
    coarse = _record(spec, sha, "wind10m-coarse", "hero-coarse", ["b.webp"], vmax=23.6)
    with pytest.raises(encode.RampIdentityError, match="wind10m"):
        encode.verify_identity([fine, coarse])


def test_manifest_build_runs_the_identity_gate(specs, lut_dir):
    """build_manifest must refuse a mismatched pair even when everything else is valid."""
    spec = specs["wind10m"]
    _, sha = encode.load_lut(lut_dir / spec.lut_filename)
    fine = _record(spec, sha, "wind10m-fine", "hero-fine", ["a.webp"])
    tampered = _record(
        spec, sha, "wind10m-coarse", "hero-coarse", ["b.webp"],
        identity=encode.identity_checksum(spec.variable, "f" * 64, spec.vmin, spec.vmax, spec.alpha),
    )
    run = {
        "id": "test",
        "kind": "dev-sample",
        "model": {"prognostic": "x", "downscaling": "y"},
        "generatedNote": "test",
    }
    with pytest.raises(encode.RampIdentityError):
        manifest.build_manifest(run, ["2021-01-01T00:00:00Z"], [fine, tampered], specs)
