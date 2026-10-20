import numpy as np
import pytest

from floodline.stages.sentinel1 import agreement, otsu


def test_otsu_splits_water_from_land_backscatter():
    rng = np.random.default_rng(0)
    water = rng.normal(-22.0, 1.5, 4000)  # calm water: very low VV backscatter
    land = rng.normal(-9.0, 2.0, 6000)
    t = otsu(np.concatenate([water, land, [np.nan]]))
    assert -18.0 < t < -12.0
    assert (water < t).mean() > 0.99 and (land > t).mean() > 0.99


def test_agreement_counts_only_valid_pixels():
    radar = np.array([[1, 1, 0], [0, 1, 0]], dtype=bool)
    ems = np.array([[1, 0, 0], [0, 1, 1]], dtype=bool)
    valid = np.array([[1, 1, 1], [1, 1, 0]], dtype=bool)  # the last pixel is outside the mapped area
    a = agreement(radar, ems, valid)
    assert a["iou"] == pytest.approx(2 / 3, abs=1e-3)  # tp 2, fp 1, fn 0 once the invalid pixel is dropped
    assert a["precision"] == pytest.approx(2 / 3, abs=1e-3) and a["recall"] == 1.0
    assert a["radar_water_share"] == pytest.approx(3 / 5, abs=1e-3)
