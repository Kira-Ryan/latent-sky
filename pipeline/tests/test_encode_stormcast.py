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
