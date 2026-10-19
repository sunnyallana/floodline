from itertools import pairwise

import numpy as np
import pytest

from floodline.depth import DEPTH_BINS_CM, bin_depth, combine, estimate_from_object, submerged_fraction


def scene(obj_rows=(20, 120), water_from=80, h=160, w=100):
    obj = np.zeros((h, w), bool)
    obj[obj_rows[0] : obj_rows[1], 40:60] = True
    water = np.zeros((h, w), bool)
    water[water_from:, :] = True
    return obj, water


def test_submerged_fraction_reads_waterline():
    obj, water = scene()
    assert submerged_fraction(obj, water) == pytest.approx(0.4)


def test_no_water_touching_object_is_zero():
    obj, water = scene(water_from=130)
    assert submerged_fraction(obj, water) == 0.0


def test_empty_object_is_zero():
    _, water = scene()
    assert submerged_fraction(np.zeros_like(water), water) == 0.0


def test_estimate_scales_reference_height():
    e = estimate_from_object("person", 0.4)
    assert e is not None
    assert (e.depth_cm_low, e.depth_cm_high) == (60.0, 72.0)
    assert estimate_from_object("unknown-thing", 0.5) is None


@pytest.mark.parametrize(
    "cm,expected",
    [(0, "dry"), (10, "ankle"), (35, "knee"), (75, "waist"), (120, "chest"), (300, "above_head")],
)
def test_bins(cm, expected):
    assert bin_depth(cm) == expected


def test_bins_are_contiguous():
    for (_, _, hi), (_, lo, _) in pairwise(DEPTH_BINS_CM):
        assert hi == lo


def test_combine_median_and_spread():
    ests = [estimate_from_object("person", 0.4), estimate_from_object("car wheel", 1.0)]
    out = combine(ests)
    assert out["n_refs"] == 2 and out["depth_bin"] == "waist"
    assert out["spread_cm"] == pytest.approx(abs(ests[0].depth_cm_mid - ests[1].depth_cm_mid))
    assert combine([]) == {"depth_cm": None, "depth_bin": None, "n_refs": 0, "spread_cm": None}
