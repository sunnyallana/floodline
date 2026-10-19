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
    hexes = aggregate(_reports())
    assert len(hexes) == 2
    top = hexes.sort_values("n_reports", ascending=False).iloc[0]
    assert top.n_reports == 2 and top.severity_max == pytest.approx(0.9) and top.people_in_water == 3


def test_aggregate_accepts_located_but_unassessed_photos():
    photos = pd.DataFrame({"lat": [24.8607, 24.8608], "lon": [67.0011, 67.0012], "location_confidence": [0.95, 0.95]})
    hexes = aggregate(photos)
    assert len(hexes) == 1 and hexes.n_reports.iloc[0] == 2 and np.isnan(hexes.severity_max.iloc[0])


def test_score_gates_dry_hexes_and_ranks_exposure():
    hexes = pd.DataFrame(
        {
            "h3": ["a", "b", "c", "d"],
            "satellite_water_fraction": [0.9, 0.2, 0.0, 0.0],
            "buildings_in_flood": [0, 30, 0, 0],
            "buildings_total": [0, 40, 50, 50],
            "cut_off": [False, False, True, False],
            "delay_min": [0.0, 0.0, np.nan, 0.0],
            "reach_dry_min": [2.0, 2.0, 3.0, 2.0],
        }
    )
    ranked = score(hexes).set_index("h3")
    assert ranked.loc["d", "priority"] == 0  # dry and reachable: nothing to do
    assert ranked.loc["b", "priority"] > ranked.loc["a", "priority"]  # flooded street beats flooded meadow
    assert ranked.loc["c", "priority"] > ranked.loc["a", "priority"]  # dry but cut off with 50 buildings
    assert ranked.loc["c", "buildings_affected"] == 50 and ranked.loc["b", "buildings_affected"] == 30
    assert ranked.priority.between(0, 1).all()


def test_photos_raise_confidence_not_priority():
    base = pd.DataFrame({"h3": ["a"], "satellite_water_fraction": [0.5], "buildings_in_flood": [10]})
    with_photo = base.assign(n_reports=[2], location_confidence=[0.95])
    a, b = score(base).iloc[0], score(with_photo).iloc[0]
    assert b.priority == a.priority and b.confidence > a.confidence


def test_photo_depth_overrides_satellite_hazard():
    hexes = pd.DataFrame({"h3": ["a"], "satellite_water_fraction": [0.1], "severity_max": [0.9]})
    ranked = score(hexes)
    assert ranked.hazard.iloc[0] == pytest.approx(0.9)
    assert ranked.photo_satellite_agreement.iloc[0] == pytest.approx(0.2)
