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
    assert top.n_reports == 2 and top.n_devices == 2
    assert top.depth_claim == "waist"  # median of chest and waist, rounded down to a class
    assert top.depth_agreeing_devices == 2 and top.people_in_water == 3


def test_aggregate_accepts_located_but_unassessed_photos():
    photos = pd.DataFrame({"lat": [24.8607, 24.8608], "lon": [67.0011, 67.0012], "location_confidence": [0.95, 0.95]})
    hexes = aggregate(photos)
    assert len(hexes) == 1 and hexes.n_reports.iloc[0] == 2 and hexes.depth_claim.iloc[0] is None


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
    with_photo = base.assign(n_reports=[2], n_devices=[2], location_confidence=[0.95])
    a, b = score(base).iloc[0], score(with_photo).iloc[0]
    assert b.priority == a.priority and b.confidence > a.confidence


# ---------------------------------------------------------------- report trust and corroboration
CELL = "882830828bfffff"  # any valid res-8 cell; score() only needs ids


def _claim(**over):
    row = {
        "h3": CELL,
        "satellite_water_fraction": 0.0,
        "satellite_water_near": False,
        "buildings_in_flood": 0,
        "depth_claim": "chest",
        "depth_agreeing_devices": 1,
        "people_in_water": 0,
        "people_elevated": 4,
        "n_reports": 1,
        "n_devices": 1,
        "location_confidence": 0.95,
    }
    return pd.DataFrame([{**row, **over}])


def test_a_single_report_in_a_dry_area_asks_for_verification_not_a_boat():
    ranked = score(_claim()).iloc[0]
    assert ranked.priority == 0 and ranked.hazard == 0
    assert bool(ranked.needs_verification) and not bool(ranked.corroborated)
    assert ranked.verify_score == pytest.approx(0.9)  # chest-deep claim, so it is checked first


def test_two_agreeing_devices_count_where_the_satellite_has_not_mapped():
    ranked = score(_claim(satellite_water_fraction=np.nan, depth_agreeing_devices=2, people_devices=2, n_devices=2))
    row = ranked.iloc[0]
    assert bool(row.corroborated) and not bool(row.needs_verification)
    assert row.hazard == pytest.approx(0.9) and row.priority > 0.5  # chest-deep, four people up high


def test_colluding_devices_cannot_overrule_a_dry_satellite_map():
    row = score(_claim(depth_agreeing_devices=2, people_devices=2, n_devices=2)).iloc[0]
    assert row.priority == 0 and bool(row.needs_verification)


def test_satellite_water_confirms_depth_but_never_people():
    row = score(_claim(satellite_water_fraction=0.3, satellite_water_near=True, buildings_in_flood=0)).iloc[0]
    assert row.hazard == pytest.approx(0.9)  # depth backed by the satellite
    assert row.exposure == 0 and bool(row.needs_verification)  # four claimed people wait for a second witness


def test_model_people_count_is_used_instead_of_the_claim():
    row = score(_claim(satellite_water_near=True, people_assessed=2)).iloc[0]
    assert row.exposure == pytest.approx(1 - np.exp(-2 * 5 / 20), abs=1e-3)


def test_satellite_water_nearby_corroborates_one_report():
    ranked = score(_claim(satellite_water_near=True)).iloc[0]
    assert bool(ranked.corroborated) and ranked.priority > 0


def test_model_depth_is_a_claim_not_evidence():
    # The depth model does no better than a constant guess (results/vlm_benchmark.json): alone, it only asks
    # for verification; backed by satellite water nearby, it counts.
    alone = score(_claim(depth_claim=None, depth_assessed="waist", people_elevated=0)).iloc[0]
    assert alone.priority == 0 and bool(alone.needs_verification) and alone.verify_score == pytest.approx(0.7)
    backed = score(_claim(depth_claim=None, depth_assessed="waist", people_elevated=0, satellite_water_near=True))
    assert backed.iloc[0].hazard == pytest.approx(0.7) and backed.iloc[0].priority > 0


def test_reports_under_review_or_rejected_never_become_evidence():
    reports = pd.DataFrame(
        {
            "lat": [24.8607] * 3,
            "lon": [67.0011] * 3,
            "depth_class": ["above_head", "above_head", "chest"],
            "device_id": ["d1", "d2", "d3"],
            "trust": ["rejected", "review", "review"],
            "people_in_water": [9, 9, 9],
        }
    )
    hexes = aggregate(reports)
    assert hexes.n_reports.iloc[0] == 0 and hexes.n_unverified.iloc[0] == 2  # the rejected one is not even counted
    ranked = score(hexes.assign(satellite_water_fraction=0.0)).iloc[0]
    assert ranked.priority == 0 and bool(ranked.needs_verification)


def test_one_device_cannot_corroborate_itself_and_people_are_not_summed():
    reports = pd.DataFrame(
        {
            "lat": [24.8607] * 4,
            "lon": [67.0011] * 4,
            "depth_class": ["chest"] * 4,
            "device_id": ["same"] * 4,
            "people_in_water": [3, 3, 2, 3],
        }
    )
    hexes = aggregate(reports)
    assert hexes.n_reports.iloc[0] == 4 and hexes.n_devices.iloc[0] == 1
    assert hexes.depth_agreeing_devices.iloc[0] == 1 and hexes.people_in_water.iloc[0] == 3
    assert score(hexes.assign(satellite_water_fraction=0.0)).priority.iloc[0] == 0


def test_satellite_water_near_includes_neighbours():
    from floodline.stages.fuse import satellite_water_near

    centre = h3.latlng_to_cell(24.86, 67.0, 9)
    neighbour = next(c for c in h3.grid_disk(centre, 1) if c != centre)
    far = h3.latlng_to_cell(24.90, 67.1, 9)
    cells = pd.Series([centre, neighbour, far])
    near = satellite_water_near(cells, pd.Series([0.0, 0.4, 0.0]))
    assert near.tolist() == [True, True, False]
