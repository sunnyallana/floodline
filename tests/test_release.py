import pandas as pd
from PIL import Image

from floodline.config import Paths
from floodline.stages.release import build


def test_bundle_is_an_imagefolder_with_card(tmp_path, monkeypatch):
    cvfd = tmp_path / "cvfd"
    (cvfd / "images" / "test" / "flooded").mkdir(parents=True)
    monkeypatch.setenv("FLOODLINE_CVFD", str(cvfd))
    paths = Paths(root=tmp_path)
    paths.data.mkdir(parents=True)
    rows = []
    for i, (bin_, note) in enumerate([("knee", "person; low confidence"), ("unknown", "aerial")]):
        rel = f"images/test/flooded/flooded_{i}.jpg"
        Image.new("RGB", (8, 8)).save(cvfd / rel)
        rows.append({"image_id": f"flooded_{i}", "image_path": rel, "depth_bin": bin_, "reference": "", "note": note})
    pd.DataFrame(rows).to_csv(paths.depth_labels, index=False)

    out = tmp_path / "bundle"
    meta = build(paths, out, repo="me/x")
    assert meta.columns[0] == "file_name" and meta.low_confidence.tolist() == [True, False]
    assert (out / "images" / "flooded_0.jpg").exists() and (out / "images" / "metadata.csv").exists()
    card = (out / "README.md").read_text("utf-8")
    assert card.startswith("---\nlicense: cc-by-sa-4.0") and "AI\nassistant" in card and "me/x" in card
    assert "| `knee` | 20-50 cm | 1 |" in card
