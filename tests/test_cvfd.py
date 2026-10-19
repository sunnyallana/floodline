import numpy as np
import pandas as pd
import pytest
from PIL import Image

from floodline.cvfd import MASK_CLASSES, eval_split, index_cvfd, summarise
from floodline.stages.sheets import stratified_sample


@pytest.fixture
def tiny_cvfd(tmp_path):
    """Three flooded and two non-flooded images across splits, with masks for one flooded image."""
    layout = [
        ("train", "flooded", "f1"),
        ("train", "non-flooded", "n1"),
        ("test", "flooded", "f2"),
        ("test", "flooded", "f3"),
        ("val", "non-flooded", "n2"),
    ]
    for split, label, stem in layout:
        d = tmp_path / "images" / split / label
        d.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.zeros((8, 8, 3), np.uint8)).save(d / f"{stem}.jpg")
    for cls in MASK_CLASSES:
        d = tmp_path / "masks" / cls / "test" / "flooded"
        d.mkdir(parents=True)
        Image.fromarray(np.zeros((8, 8), np.uint8)).save(d / "f2.png")
    return tmp_path


def test_index_rows_and_mask_paths(tiny_cvfd):
    df = index_cvfd(tiny_cvfd)
    assert len(df) == 5 and df.flooded.sum() == 3
    f2 = df.set_index("image_id").loc["f2"]
    assert f2.mask_flood == "masks/flood/test/flooded/f2.png"
    assert pd.isna(df.set_index("image_id").loc["f3"].mask_flood)
    assert "/" in f2.image_path and "\\" not in f2.image_path


def test_summary_counts(tiny_cvfd):
    s = summarise(index_cvfd(tiny_cvfd)).set_index(["split", "label"])
    assert s.loc[("test", "flooded"), "images"] == 2
    assert s.loc[("test", "flooded"), "with_flood_mask"] == 1


def test_eval_split_and_limit(tiny_cvfd):
    df = index_cvfd(tiny_cvfd)
    assert list(eval_split(df).image_id) == ["f2", "f3"]
    assert len(eval_split(df, limit=1)) == 1


def test_missing_root_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        index_cvfd(tmp_path / "nope")


def test_stratified_sample_keeps_split_mix(tiny_cvfd):
    df = index_cvfd(tiny_cvfd)
    s = stratified_sample(df[df.flooded == 1], n=3, seed=0)
    assert set(s.split) == {"train", "test"} and len(s) == 3
