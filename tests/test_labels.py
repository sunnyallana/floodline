import pandas as pd
import pytest

from floodline.config import Paths
from floodline.stages import labels


def test_kappa_perfect_chance_and_weighted():
    a = pd.Series(["dry", "knee", "waist", "chest"] * 10)
    assert labels.cohen_kappa(a, a, labels.ORDERED) == pytest.approx(1.0)
    # off by one class everywhere: unweighted kappa is low, linearly weighted kappa credits the near-miss
    shifted = a.map({"dry": "ankle", "knee": "waist", "waist": "chest", "chest": "above_head"})
    assert labels.cohen_kappa(a, shifted, labels.ORDERED) < 0.1
    assert labels.cohen_kappa(a, shifted, labels.ORDERED, weighted=True) > labels.cohen_kappa(
        a, shifted, labels.ORDERED
    )


def test_kappa_matches_a_hand_computed_case():
    # 2x2: p_o = 0.7, p_e = 0.5 * 0.6 + 0.5 * 0.4 = 0.5, kappa = 0.4
    a = pd.Series(["dry"] * 5 + ["knee"] * 5)
    b = pd.Series(["dry"] * 4 + ["knee"] * 1 + ["dry"] * 2 + ["knee"] * 3)
    assert labels.cohen_kappa(a, b, ("dry", "knee")) == pytest.approx(0.4)


def test_run_writes_agreement_and_reviewed_labels(tmp_path):
    paths = Paths(root=tmp_path)
    paths.data.mkdir(parents=True)
    paths.results.mkdir(parents=True)
    pd.DataFrame(
        {"image_id": ["a", "b", "c"], "split": "test", "image_path": "x", "depth_bin": ["knee", "unknown", "dry"],
         "reference": ["person", "", "car"], "note": ""}
    ).to_csv(paths.depth_labels, index=False)  # fmt: skip
    review = tmp_path / "review.csv"
    pd.DataFrame(
        {
            "image_id": ["a", "b", "c"],
            "human_depth_bin": ["waist", "unknown", "dry"],
            "human_reference": ["person", "none", "car"],
            "human_note": "",
            "labelled_at": "",
            "final_depth_bin": ["knee", "unknown", "dry"],
            "adjudicated": [True, False, False],
        }
    ).to_csv(review, index=False)
    s = labels.run(paths, review)
    assert s["blind_vs_first_pass"]["raw_agreement"] == pytest.approx(2 / 3, abs=1e-3)
    assert s["final_vs_first_pass"]["raw_agreement"] == 1.0 and s["adjudicated"] == 1
    out = pd.read_csv(paths.data / "depth_labels_reviewed.csv")
    assert out.set_index("image_id").loc["a", ["depth_bin", "depth_bin_first_pass", "human_depth_bin"]].tolist() == [
        "knee",
        "knee",
        "waist",
    ]
