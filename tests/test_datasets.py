import pandas as pd
import pytest

from floodline import datasets
from floodline.config import Paths


def test_results_namespace(tmp_path):
    assert Paths(root=tmp_path).results == tmp_path / "results"
    assert Paths(root=tmp_path, namespace="euflood").results == tmp_path / "results" / "euflood"
    assert Paths(root=tmp_path, namespace="euflood").data == tmp_path / "data"  # inputs are shared


def test_euflood_loader_keeps_mappable_rows(tmp_path):
    paths = Paths(root=tmp_path)
    paths.data.mkdir(parents=True)
    pd.DataFrame(
        {
            "image_id": ["euf_1", "euf_2", "euf_3"],
            "image_path": ["imgs/imgs_large/1.jpg", None, "imgs/imgs_large/3.jpg"],
            "location_tier": ["gps", "gps", "none"],
            "rel_depth": [True, True, False],
        }
    ).to_parquet(paths.data / "eu_flood_2013_index.parquet")
    ds = datasets.load(paths, "euflood")
    assert list(ds.frame.image_id) == ["euf_1"] and not ds.has_masks
    assert ds.root == tmp_path / "data" / "external" / "eu-flood-2013"


def test_unknown_dataset_and_missing_index(tmp_path):
    paths = Paths(root=tmp_path)
    with pytest.raises(ValueError):
        datasets.load(paths, "nope")
    with pytest.raises(FileNotFoundError):
        datasets.load(paths, "euflood")
