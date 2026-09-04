"""The FSS maths, pinned on synthetic fields where the right answer is known."""

import numpy as np
import pytest

from latentsky import verify


@pytest.fixture
def storm():
    """A 60x80 field with one 6x6 block of 50 dBZ and a valid mask covering it all."""
    f = np.zeros((60, 80), dtype=np.float32)
    f[20:26, 30:36] = 50.0
    return f, np.ones_like(f, dtype=bool)


def test_perfect_forecast_scores_one(storm):
    f, v = storm
    assert verify.fss(f, f, v, 40.0, 11) == pytest.approx(1.0)


def test_empty_forecast_scores_zero(storm):
    f, v = storm
    assert verify.fss(f, np.zeros_like(f), v, 40.0, 11) == pytest.approx(0.0)


def test_nothing_anywhere_is_undefined_not_perfect():
    z = np.zeros((10, 10), dtype=np.float32)
    assert np.isnan(verify.fss(z, z, np.ones_like(z, dtype=bool), 40.0, 5))


def test_displacement_is_forgiven_only_at_scale(storm):
    """The whole point of FSS: a storm 8 cells off scores zero at the gridpoint and
    climbs as the neighbourhood grows past the displacement."""
    f, v = storm
    o = np.roll(f, 8, axis=1)
    scores = [verify.fss(f, o, v, 40.0, w) for w in (1, 5, 11, 21, 41)]
    assert scores[0] == pytest.approx(0.0)
    assert all(b >= a for a, b in zip(scores, scores[1:]))
    assert scores[-1] > 0.8


def test_masked_cells_carry_no_mass(storm):
    """Exceedances inside the invalid region must not leak into the score."""
    f, v = storm
    o = f.copy()
    o[50:56, 60:66] = 60.0          # an observed storm...
    v2 = v.copy()
    v2[48:58, 58:68] = False        # ...entirely outside radar coverage
    assert verify.fss(f, o, v2, 40.0, 11) == pytest.approx(1.0)


def test_probabilistic_reduces_to_binary_for_a_certain_ensemble(storm):
    f, v = storm
    p = (f >= 40.0).astype(np.float64)
    assert verify.fss_probabilistic(p, f, v, 40.0, 11) == pytest.approx(verify.fss(f, f, v, 40.0, 11))


def test_coverage_and_centroid(storm):
    f, v = storm
    assert verify.coverage(f, v, 40.0) == pytest.approx(36 / (60 * 80))
    lat = np.linspace(45.0, 31.0, 60)
    lon = np.linspace(-110.0, -85.0, 80)
    c = verify.centroid(f, v, lat, lon, 40.0)
    assert c["cells"] == 36
    assert lat[25] < c["lat"] < lat[20]
    assert lon[30] < c["lon"] < lon[35]
    assert verify.centroid(np.zeros_like(f), v, lat, lon, 40.0) is None


def test_km_between():
    a = {"lat": 35.0, "lon": -92.0}
    assert verify.km_between(a, a) == pytest.approx(0.0)
    assert verify.km_between(a, {"lat": 36.0, "lon": -92.0}) == pytest.approx(111.2, abs=0.5)
    assert verify.km_between(a, None) is None


def test_to_180():
    assert list(verify.to_180(np.array([250.37, 274.58, 116.0]))) == pytest.approx([-109.63, -85.42, 116.0])


def _results(by_window, useful=0.5, windows=(2.4, 12.0, 26.3, 50.3, 98.2), leads=None):
    """A results dict shaped like score()'s, with FSS values we choose."""
    n = leads if leads is not None else len(by_window)
    return {
        "windows_km": list(windows),
        "leads": [
            {"lead_h": h, "fss": {"40": {"by_window": list(by_window[h]), "fss_useful": useful}}}
            for h in range(n)
        ],
    }


def test_headline_reports_the_smallest_useful_scale():
    # Useful only at the two largest windows, and only in the later hours.
    rows = [[0.1, 0.2, 0.3, 0.4, 0.45]] * 2 + [[0.1, 0.2, 0.3, 0.45, 0.9]] * 3
    h = verify.headline(_results(rows))
    assert h["usefulScaleKm"] == 98.2
    assert h["usefulHours"] == 3 and h["scoredHours"] == 3


def test_headline_excludes_the_spin_up_hours():
    """A model handed an analysis scores trivially against it before it has done
    any work; counting those hours would flatter the run."""
    rows = [[0.99] * 5, [0.99] * 5] + [[0.1] * 5] * 3
    h = verify.headline(_results(rows))
    assert h["usefulScaleKm"] is None, "spin-up hours leaked into the headline"
    assert h["scoredHours"] == 3


def test_headline_says_none_rather_than_omitting_it():
    """No useful skill at any scale is a real result and must be publishable."""
    h = verify.headline(_results([[0.1] * 5] * 5))
    assert h["usefulScaleKm"] is None
    assert h["usefulHours"] == 0
    assert h["largestScaleKm"] == 98.2


def test_headline_prefers_the_smallest_scale_that_works():
    rows = [[0.1, 0.9, 0.9, 0.9, 0.9]] * 5
    assert verify.headline(_results(rows))["usefulScaleKm"] == 12.0
