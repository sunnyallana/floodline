import numpy as np
import pandas as pd

from floodline.stages.waterline import ORDER, depth_consistent, estimate_with_offset, score_against_labels


def test_window_offset_adds_sill_height():
    e = estimate_with_offset("window", 0.5, base_cm=100.0)
    assert e is not None and e.depth_cm_low == 100 + 45 and e.depth_cm_high == 100 + 55
    assert estimate_with_offset("window", 0.0, 100.0) is None


def test_depth_consistency_vetoes_distant_objects():
    obj = np.zeros((60, 60), bool)
    obj[10:30, 20:30] = True
    water = np.zeros((60, 60), bool)
    water[25:, :] = True
    near = np.full((60, 60), 10.0, np.float32)
    assert depth_consistent(obj, water, near)
    far_object = near.copy()
    far_object[obj] = 40.0  # object four times further away than the water beside it
    assert not depth_consistent(obj, water, far_object)


def test_benchmark_scoring_counts_exact_and_adjacent():
    pred = pd.DataFrame({"image_id": ["a", "b", "c", "d"], "depth_bin": ["knee", "waist", "dry", None]})
    labels = pd.DataFrame({"image_id": ["a", "b", "c", "d"], "depth_bin": ["knee", "chest", "above_head", "knee"]})
    s = score_against_labels(pred, labels)
    assert s["n"] == 3 and s["exact"] == round(1 / 3, 4) and s["within_one"] == round(2 / 3, 4)
    assert ORDER[0] == "dry"
