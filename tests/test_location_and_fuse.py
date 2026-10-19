from datetime import datetime

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from floodline.location import Location, exif_location, h3_index
from floodline.stages.fuse import aggregate, score

h3 = pytest.importorskip("h3")


def _jpeg_with_gps(path, lat=24.8607, lon=67.0011, south=False, west=False):
    im = Image.new("RGB", (16, 16), "gray")
    exif = Image.Exif()
    gps = {
        1: "S" if south else "N",
        2: (abs(lat) // 1, (abs(lat) % 1) * 60 // 1, ((abs(lat) % 1) * 60 % 1) * 60),
        3: "W" if west else "E",
        4: (abs(lon) // 1, (abs(lon) % 1) * 60 // 1, ((abs(lon) % 1) * 60 % 1) * 60),
    }
    exif[0x8825] = gps
    exif[306] = "2022:08:28 14:05:00"
    im.save(path, exif=exif)


def test_exif_gps_is_tier_one(tmp_path):
    p = tmp_path / "a.jpg"
    _jpeg_with_gps(p)
    loc = exif_location(p)
    assert loc is not None and loc.tier == "gps" and loc.confidence == 0.95
    assert loc.lat == pytest.approx(24.8607, abs=1e-3) and loc.lon == pytest.approx(67.0011, abs=1e-3)
    assert loc.h3_cell == h3.latlng_to_cell(loc.lat, loc.lon, 8)
    assert loc.captured_at == datetime(2022, 8, 28, 14, 5)


def test_hemispheres_flip_sign(tmp_path):
    p = tmp_path / "b.jpg"
    _jpeg_with_gps(p, lat=33.9, lon=18.4, south=True, west=True)
    loc = exif_location(p)
    assert loc.lat < 0 and loc.lon < 0


def test_file_without_gps_has_no_location(tmp_path):
    p = tmp_path / "c.jpg"
    Image.new("RGB", (8, 8)).save(p)
    assert exif_location(p) is None


def test_location_rejects_out_of_range():
    with pytest.raises(ValueError):
        Location.from_tier(95.0, 0.0, "gps")
    assert h3_index(0.0, 0.0) is not None


def _reports():
    base = dict(
        people_visible=1, people_in_water=0, people_on_roofs_or_elevated=0, confidence=0.9, location_confidence=0.95
    )
    return pd.DataFrame(
        [
            {
                "lat": 24.8607,
                "lon": 67.0011,
                "depth_class": "chest",
                "captured_at": "2022-08-28T10:00:00Z",
                **base,
                "people_in_water": 3,
            },
            {"lat": 24.8608, "lon": 67.0012, "depth_class": "waist", "captured_at": "2022-08-28T11:00:00Z", **base},
            {"lat": 25.3960, "lon": 68.3578, "depth_class": "ankle", "captured_at": "2022-08-27T09:00:00Z", **base},
            {"lat": np.nan, "lon": np.nan, "depth_class": "knee", "captured_at": None, **base},
        ]
    )


def test_aggregate_groups_nearby_reports_and_drops_unlocated():
    hexes = aggregate(_reports(), now=pd.Timestamp("2022-08-28T12:00:00Z"))
    assert len(hexes) == 2
    top = hexes.sort_values("n_reports", ascending=False).iloc[0]
    assert top.n_reports == 2 and top.severity_max == pytest.approx(0.9) and top.people_in_water == 3
    assert top.hours_since_last == pytest.approx(1.0)


def test_score_ranks_people_and_depth_first_and_lowers_confidence_without_layers():
    hexes = aggregate(_reports(), now=pd.Timestamp("2022-08-28T12:00:00Z"))
    ranked = score(hexes)
    assert ranked.iloc[0].n_reports == 2
    assert ranked.priority.min() >= 0 and ranked.priority.max() <= 1
    assert ranked.confidence.max() < 0.8  # no satellite or reach layers supplied
    with_layers = hexes.assign(satellite_water_fraction=[0.8, 0.1], reach_minutes=[120.0, 10.0])
    ranked2 = score(with_layers)
    assert ranked2.confidence.max() > ranked.confidence.max()
    assert "photo_satellite_agreement" in ranked2
