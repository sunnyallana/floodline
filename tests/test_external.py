import json

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from floodline.stages.external import build_table


@pytest.fixture
def tiny_euflood(tmp_path):
    meta = tmp_path / "meta"
    (meta / "relevance").mkdir(parents=True)
    imgs = tmp_path / "imgs" / "imgs_large"
    imgs.mkdir(parents=True)
    records = [
        {
            "pageid": 1,
            "title": "File:Dresden a.jpg",
            "capture_time": "2013-06-05T10:00:00",
            "coordinates": {"lat": 51.05, "lon": 13.74},
            "license": "CC BY-SA 3.0",
            "artist": "A",
        },
        {
            "pageid": 2,
            "title": "File:Passau b.jpg",
            "capture_time": "2013-06-04T09:00:00",
            "license": "CC0",
            "artist": "B",
        },
        {
            "pageid": 3,
            "title": "File:Missing c.jpg",
            "coordinates": {"lat": 48.57, "lon": 13.46},
            "license": "CC BY 3.0",
        },
    ]
    (meta / "metadata.json").write_text(json.dumps(records), encoding="utf-8")
    (meta / "relevance" / "depth.txt").write_text("1\n3\n", encoding="utf-8")
    (meta / "relevance" / "flooding.txt").write_text("1\n2\n", encoding="utf-8")
    for pid in (1, 2):
        Image.fromarray(np.zeros((8, 8, 3), np.uint8)).save(imgs / f"{pid}.jpg")
    return tmp_path


def test_table_joins_metadata_relevance_and_files(tiny_euflood):
    df = build_table(tiny_euflood).set_index("pageid")
    assert len(df) == 3
    a = df.loc["1"]
    assert a.location_tier == "gps" and a.location_confidence == 0.95 and a.rel_depth and a.rel_flooding
    assert a.image_path == "imgs/imgs_large/1.jpg" and a.title == "Dresden a.jpg"
    assert df.loc["2"].location_tier == "none" and not df.loc["2"].rel_depth
    assert pd.isna(df.loc["3"].image_path)  # located but the file is absent: not mappable


def test_h3_cell_present_only_for_located(tiny_euflood):
    pytest.importorskip("h3")
    df = build_table(tiny_euflood).set_index("pageid")
    assert isinstance(df.loc["1"].h3, str) and pd.isna(df.loc["2"].h3)
