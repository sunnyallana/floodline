import numpy as np
import pandas as pd
import pytest

gpd = pytest.importorskip("geopandas")
from shapely.geometry import LineString, box  # noqa: E402

from floodline import osm  # noqa: E402
from floodline.stages import sensitivity, validate  # noqa: E402
from floodline.stages.fuse import Weights  # noqa: E402


@pytest.mark.parametrize(
    ("tags", "drivable"),
    [
        ({"highway": "residential"}, True),
        ({"highway": "primary", "service": "driveway"}, False),
        ({"highway": "footway"}, False),
        ({"highway": "service"}, False),
        ({"highway": "tertiary", "access": "private"}, False),
        ({"highway": "unclassified", "motor_vehicle": "no"}, False),
        ({"highway": "residential", "area": "yes"}, False),
        ({"building": "yes"}, False),
    ],
)
def test_drive_filter_matches_osmnx(tags, drivable):
    assert osm.is_drivable(tags) is drivable


def test_read_poly_with_hole(tmp_path):
    poly = tmp_path / "region.poly"
    poly.write_text(
        "region\n1\n 0 0\n 10 0\n 10 10\n 0 10\nEND\n!hole\n 4 4\n 6 4\n 6 6\n 4 6\nEND\nEND\n", encoding="utf-8"
    )
    g = osm.read_poly(poly)
    assert g.area == pytest.approx(96.0)
    pbf = tmp_path / "region.osm.pbf"
    pbf.write_bytes(b"")  # pbf_covers reads the .poly next to it and never opens the empty pbf
    assert osm.pbf_covers(pbf, (1, 1, 3, 3)) and not osm.pbf_covers(pbf, (3, 3, 5, 5))


def test_retry_moves_across_mirrors_and_gives_up():
    pytest.importorskip("osmnx")
    calls = []

    class TransientError(Exception):
        pass

    TransientError.__name__ = "ResponseStatusCodeError"

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise TransientError("502 Bad Gateway")
        return "ok"

    assert osm.call(flaky, sleep=lambda s: None) == "ok" and len(calls) == 3
    with pytest.raises(ValueError):
        osm.call(lambda: (_ for _ in ()).throw(ValueError("not transient")), sleep=lambda s: None)


def test_road_rule_counts_overlap_against_grades():
    roads = gpd.GeoDataFrame(
        {"damage_gra": ["Destroyed", "No visible damage", "Possibly damaged", "Not Analysed"]},
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(0, 50), (100, 50)]), LineString([(200, 0), (300, 0)]),
                  LineString([(0, 80), (100, 80)])],
    )  # fmt: skip
    flood = box(40, -10, 60, 60)  # crosses the first two roads for 20 m each, misses the third
    table = validate.road_rule(roads, flood, thresholds=(0.0, 15.0, 25.0)).set_index(["target", "threshold_m"])
    assert table.loc[("severe", 15.0), "segments"] == 3  # "Not Analysed" is excluded
    assert table.loc[("severe", 15.0), ["tp", "fp", "fn"]].tolist() == [1, 1, 0]
    assert table.loc[("affected", 15.0), ["tp", "fp", "fn"]].tolist() == [1, 1, 1]
    assert table.loc[("severe", 25.0), "tp"] == 0  # 20 m inside is not enough at 25 m


def test_capture_gives_no_credit_for_ties():
    target = pd.Series([0, 0, 0, 10])
    assert validate.capture(pd.Series([1.0, 1.0, 1.0, 1.0]), target, (0.25,))["top25"] == 0.0
    assert validate.capture(pd.Series([0.1, 0.2, 0.3, 0.9]), target, (0.25,))["top25"] == 1.0


def test_damage_per_cell_and_rank_metrics():
    h3 = pytest.importorskip("h3")
    pts = gpd.GeoDataFrame(
        {"damage_gra": ["Destroyed", "Damaged", "Possibly damaged"]},
        geometry=gpd.points_from_xy([6.80, 6.80, 6.81], [50.80, 50.80, 50.81]),
        crs="EPSG:4326",
    )
    dmg = validate.damage_per_cell(pts, 9).set_index("h3")
    assert dmg.loc[h3.latlng_to_cell(50.80, 6.80, 9)].tolist() == [2, 2]
    hexes = pd.DataFrame({"h3": list("abcd"), "priority": [0.9, 0.1, 0.0, 0.0], "severe": [5, 0, 0, 1]})
    m = validate.rank_metrics(hexes, "severe", scores=("priority",))
    assert m["target_total"] == 6 and m["priority"]["top20"] == pytest.approx(5 / 6, abs=1e-3)


def test_weight_variants_are_normalised_and_complete():
    variants = sensitivity.weight_variants()
    assert len(variants) == 5**3 + 3 * 2
    for _, w in variants[:125]:
        assert w.exposure + w.hazard + w.access == pytest.approx(1.0)
    assert dict(variants)["w1/1/1"] == Weights()


def test_compare_top_k_and_spearman():
    base = pd.Series(np.arange(30, dtype=float), index=[f"h{i}" for i in range(30)])
    same = sensitivity.compare(base, base)
    assert same["top10_overlap"] == 1.0 and same["spearman_live"] == pytest.approx(1.0)
    flipped = sensitivity.compare(base, -base + 30)
    assert flipped["top5_overlap"] == 0.0
