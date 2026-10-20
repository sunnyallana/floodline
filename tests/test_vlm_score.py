import pandas as pd

from floodline.config import Paths
from floodline.stages.assess import score


def _rec(image_id: str, depth: str | None) -> dict:
    return {"image_id": image_id, **({"depth": {"depth_class": depth}} if depth else {"error": "ValueError: x"})}


def test_score_against_labels_and_same_image_baseline(tmp_path):
    paths = Paths(root=tmp_path).ensure()
    pd.DataFrame(
        {
            "image_id": ["a", "b", "c", "d", "e"],
            "depth_bin": ["ankle", "ankle", "knee", "waist", "unknown"],
        }
    ).to_csv(paths.depth_labels, index=False)
    records = [_rec("a", "ankle"), _rec("b", "knee"), _rec("c", "knee"), _rec("d", None), _rec("e", "knee")]
    out = score(paths, records, expected=5)
    assert out["n"] == 3 and out["exact"] == round(2 / 3, 4) and out["within_one"] == 1.0
    assert out["labelled_images_assessed"] == 4 and out["coverage"] == 0.75  # d failed, e is labelled unknown
    assert out["baseline"]["rule"] == "always ankle" and out["baseline"]["exact"] == round(2 / 3, 4)
    assert out["labels"] == "depth_labels.csv" and out["complete"]


def test_score_prefers_reviewed_labels(tmp_path):
    paths = Paths(root=tmp_path).ensure()
    pd.DataFrame({"image_id": ["a"], "depth_bin": ["knee"]}).to_csv(paths.depth_labels, index=False)
    pd.DataFrame({"image_id": ["a"], "depth_bin": ["ankle"]}).to_csv(
        paths.data / "depth_labels_reviewed.csv", index=False
    )
    out = score(paths, [_rec("a", "ankle")])
    assert out["labels"] == "depth_labels_reviewed.csv" and out["exact"] == 1.0 and not out["complete"]
