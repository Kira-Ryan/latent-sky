"""The ensemble probability field and the longitude wrap in encode_stormcast."""

import numpy as np
import pytest

from latentsky import encode_stormcast as es


def test_exceedance_probability_counts_members():
    m = np.zeros((4, 2, 2), dtype=np.float32)
    m[0, 0, 0] = 45.0
    m[1, 0, 0] = 40.0          # at the threshold counts
    m[2, 0, 0] = 39.9          # just under does not
    m[:, 1, 1] = 50.0
    p = es.exceedance_probability(m)
    assert p.dtype == np.float32
    assert p[0, 0] == pytest.approx(50.0)
    assert p[1, 1] == pytest.approx(100.0)
    assert p[0, 1] == 0.0


def test_exceedance_probability_refuses_a_single_member():
    with pytest.raises(es.EncodeStormcastError):
        es.exceedance_probability(np.zeros((1, 2, 2), dtype=np.float32))


def test_exceedance_probability_propagates_nan():
    m = np.full((2, 1, 1), 50.0, dtype=np.float32)
    m[1, 0, 0] = np.nan
    assert np.isnan(es.exceedance_probability(m)[0, 0])


def test_to_180():
    assert list(es.to_180(np.array([250.37, 274.58, 116.0]))) == pytest.approx([-109.63, -85.42, 116.0])


class _Hero:
    """A hero store stub whose reflectivity peaks where we say."""

    def __init__(self, peak, frames=3):
        self.peak, self.frames = peak, frames

    def get(self, key):
        if key != "hero_refc" or self.peak is None:
            return None
        return self

    def __getitem__(self, idx):
        a = np.full((8, 8), self.peak - 10.0, dtype=np.float32)
        a[0, 0] = self.peak
        return a


def test_a_run_with_convection_invites_you_into_the_storm():
    assert es.invitation_for(_Hero(58.0), 3, 3.0) == "enter the storm"
    assert es.invitation_for(_Hero(40.0), 3, 3.0) == "enter the storm"


def test_a_quiet_run_does_not_advertise_a_storm():
    """The live daily run will meet quiet days, and the site cannot invite people
    into weather that is not there."""
    assert es.invitation_for(_Hero(39.9), 3, 3.0) == "see it at 3 km"
    assert es.invitation_for(_Hero(5.0), 3, 3.0) == "see it at 3 km"


def test_the_resolution_in_the_copy_is_the_run_s_own():
    assert es.invitation_for(_Hero(10.0), 3, 2.07) == "see it at 2.07 km"


def test_a_run_without_reflectivity_keeps_the_original_copy():
    """Taiwan's typhoons carry mrr, not refc, and are named storms — the claim is
    true there and the fallback must not quietly weaken it."""
    assert es.invitation_for(_Hero(None), 3, 2.07) == "enter the storm"
