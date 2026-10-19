import numpy as np
import pandas as pd

from floodline.geometry import silhouette, touches_water, visible_fraction
from floodline.stages.waterline import ORDER, estimate_with_offset, score_against_labels


def test_window_offset_adds_sill_height():
    e = estimate_with_offset("window", 0.5, base_cm=100.0)
    assert e is not None and e.depth_cm_low == 100 + 45 and e.depth_cm_high == 100 + 55
    assert estimate_with_offset("window", 0.0, 100.0) is None


def _person(h_px, shoulder_px, top=5, left=20, size=80):
    m = np.zeros((size, size), bool)
    head = max(2, shoulder_px // 3)
    m[top : top + head, left + (shoulder_px - head) // 2 : left + (shoulder_px + head) // 2] = True
    m[top + head : top + h_px, left : left + shoulder_px] = True
    return m


def test_full_person_is_not_submerged_and_half_person_is():
    full = _person(h_px=40, shoulder_px=10)
    assert visible_fraction("person", silhouette(full)) == 1.0
    waist = _person(h_px=22, shoulder_px=10)
    assert 0.45 <= visible_fraction("person", silhouette(waist)) <= 0.6


def test_touch_requires_water_at_the_base_and_same_distance():
    obj = _person(h_px=30, shoulder_px=10)
    sil = silhouette(obj)
    water = np.zeros_like(obj)
    water[sil.bottom + 1 : sil.bottom + 4, :] = True
    near = np.full(obj.shape, 8.0, np.float32)
    assert touches_water(obj, water, near)
    far_water = near.copy()
    far_water[water] = 30.0
    assert not touches_water(obj, water, far_water)
    water_behind_only = np.zeros_like(obj)
    water_behind_only[: sil.top + 5, :] = True
    assert not touches_water(obj, water_behind_only, near)


def test_benchmark_scoring_counts_exact_and_adjacent():
    pred = pd.DataFrame({"image_id": ["a", "b", "c", "d"], "depth_bin": ["knee", "waist", "dry", None]})
    labels = pd.DataFrame({"image_id": ["a", "b", "c", "d"], "depth_bin": ["knee", "chest", "above_head", "knee"]})
    s = score_against_labels(pred, labels)
    assert s["n"] == 3 and s["exact"] == round(1 / 3, 4) and s["within_one"] == round(2 / 3, 4)
    assert ORDER[0] == "dry"
