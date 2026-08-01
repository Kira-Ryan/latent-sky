"""Shared fixtures. Tests use only synthetic data and the committed configs/LUTs —
never anything under data/dev/ (CC BY-NC-ND, local-only, absent in CI)."""

from __future__ import annotations

import pathlib

import pytest

from latentsky import ramps

PIPELINE_DIR = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def specs():
    return ramps.load_ramps(PIPELINE_DIR / "configs" / "ramps.yaml")


@pytest.fixture(scope="session")
def lut_dir(tmp_path_factory):
    """Bake LUTs into a session tmp dir so tests never depend on committed artefacts."""
    out = tmp_path_factory.mktemp("luts")
    ramps.bake(PIPELINE_DIR / "configs" / "ramps.yaml", out)
    return out
