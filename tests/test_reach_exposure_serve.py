import json

import numpy as np
import pandas as pd
import pytest

h3 = pytest.importorskip("h3")
nx = pytest.importorskip("networkx")
gpd = pytest.importorskip("geopandas")
from shapely.geometry import LineString, box  # noqa: E402

from floodline.stages.exposure import count_buildings, target_cells  # noqa: E402
from floodline.stages.fuse import combine  # noqa: E402
from floodline.stages.reach import flooded_edges, reach_table, snap  # noqa: E402
from floodline.stages.serve import build_city, visible_hexes, write_site  # noqa: E402

LAT, LON = 50.05, 14.40


def _line_graph():
    """Four nodes on a west-east street, ~140 m apart; the last segment will be closed."""
    g = nx.MultiDiGraph()
    xs = [LON + i * 0.002 for i in range(4)]
    for i, x in enumerate(xs):
        g.add_node(i, x=x, y=LAT)
    for i in range(3):
        for u, v in ((i, i + 1), (i + 1, i)):
            g.add_edge(u, v, 0, travel_time=20.0, length=140.0)
    return g, xs


def test_flooded_edges_needs_real_overlap():
    edges = gpd.GeoDataFrame(
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(0, 50), (100, 50)]), LineString([(0, 99), (40, 99)])]
    )
    flood = box(40, -10, 60, 100)  # 20 m wide strip
    out = flooded_edges(edges, flood, min_overlap_m=15.0)
    assert out.tolist() == [True, True, False]  # the third only touches the strip's edge


def test_reach_table_marks_cut_off_and_no_road():
    g, xs = _line_graph()
    cells = sorted({h3.latlng_to_cell(LAT, x, 10) for x in xs})
    empty = h3.latlng_to_cell(LAT + 0.05, LON, 10)  # no road node in this hex
    df = reach_table(g, [*cells, empty], sources=[0], flooded=[(2, 3, 0), (3, 2, 0)]).set_index("h3")
    far = h3.latlng_to_cell(LAT, xs[3], 10)
    near = h3.latlng_to_cell(LAT, xs[0], 10)
    assert bool(df.loc[far, "cut_off"]) and np.isinf(df.loc[far, "reach_flood_min"])
    assert df.loc[near, "reach_dry_min"] == 0 and not df.loc[near, "cut_off"]
    assert np.isnan(df.loc[empty, "reach_dry_min"]) and not df.loc[empty, "cut_off"]


def test_snap_drops_far_points():
    nodes = np.array([[0.0, 0.0], [1000.0, 0.0]])
    got = snap(np.array([[10.0, 0.0], [5000.0, 5000.0]]), np.array([7, 8]), nodes, max_m=500)
    assert got == [7]


def test_count_buildings_in_flood_and_in_targets():
    flood = box(LON, LAT, LON + 0.002, LAT + 0.002)
    inside = box(LON + 0.0005, LAT + 0.0005, LON + 0.0006, LAT + 0.0006)
    outside = box(LON + 0.006, LAT + 0.006, LON + 0.0061, LAT + 0.0061)
    b = gpd.GeoDataFrame(geometry=[inside, outside], crs="EPSG:4326")
    c_in = h3.latlng_to_cell(LAT + 0.00055, LON + 0.00055, 9)
    c_out = h3.latlng_to_cell(LAT + 0.00605, LON + 0.00605, 9)
    assert c_in != c_out
    df = count_buildings(b, flood, sorted({c_in, c_out})).set_index("h3")
    assert df.loc[c_in, "buildings_in_flood"] == 1
    assert df.loc[c_out, "buildings_in_flood"] == 0 and df.loc[c_out, "buildings_total"] == 1


def test_target_cells_are_wet_or_cut_off():
    layer = pd.DataFrame({"h3": ["a", "b", "c"], "satellite_water_fraction": [0.1, 0.0, 0.0]})
    reach = pd.DataFrame({"h3": ["a", "b", "c"], "cut_off": [False, True, False]})
    assert target_cells(layer, reach) == ["a", "b"]
    assert target_cells(layer) == ["a"]


def test_combine_keeps_universe_of_first_layer():
    cell = h3.latlng_to_cell(LAT, LON, 9)
    other = h3.latlng_to_cell(LAT + 0.1, LON, 9)
    sat = pd.DataFrame({"h3": [cell], "satellite_water_fraction": [0.5]})
    reach = pd.DataFrame({"h3": [cell, other], "cut_off": [True, False]})
    photos = pd.DataFrame({"lat": [LAT, LAT + 1], "lon": [LON, LON], "location_confidence": [0.95, 0.95]})
    out = combine([sat, reach], photos)
    assert out.h3.tolist() == [cell] and out.n_reports.tolist() == [1] and bool(out.cut_off.iloc[0])


def test_site_build(tmp_path):
    pkg = tmp_path / "ems" / "PKG"
    pkg.mkdir(parents=True)
    gpd.GeoDataFrame(geometry=[box(LON, LAT, LON + 0.01, LAT + 0.01)], crs="EPSG:4326").to_file(
        pkg / "X_Crisis_event_A.shp"
    )
    gpd.GeoDataFrame(geometry=[box(LON - 0.01, LAT - 0.01, LON + 0.02, LAT + 0.02)], crs="EPSG:4326").to_file(
        pkg / "X_AreaOfInterest.shp"
    )
    cell = h3.latlng_to_cell(LAT + 0.005, LON + 0.005, 9)
    dry = h3.latlng_to_cell(LAT - 0.008, LON - 0.008, 9)
    hexes = pd.DataFrame(
        {
            "h3": [cell, dry],
            "priority": [0.6, 0.0],
            "confidence": [0.5, 0.4],
            "cut_off": [True, False],
            "buildings_in_flood": [3, 0],
            "n_reports": [0, 0],
        }
    )
    assert visible_hexes(hexes).h3.tolist() == [cell]
    hexes_csv = tmp_path / "hexes.csv"
    hexes.to_csv(hexes_csv, index=False)
    photos = tmp_path / "photos.csv"
    pd.DataFrame(
        {
            "image_id": ["p1", "p2"],
            "lat": [LAT + 0.005, LAT + 1],
            "lon": [LON + 0.005, LON],
            "title": ["a.jpg", "b.jpg"],
            "license": ["CC BY-SA 3.0"] * 2,
            "source_url": ["u"] * 2,
        }
    ).to_csv(photos, index=False)
    site = tmp_path / "site"
    entry = build_city("x", "X", "", hexes_csv, tmp_path / "ems", site, photos_csv=photos)
    page = write_site(site, entry)
    assert page.exists() and "maplibre" in page.read_text("utf-8")
    cities = json.loads((site / "cities.json").read_text("utf-8"))
    assert cities[0]["stats"]["photos"] == 1 and cities[0]["stats"]["hexes_cut_off"] == 1
    fc = json.loads((site / "data" / "x" / "hexes.geojson").read_text("utf-8"))
    assert len(fc["features"]) == 1 and fc["features"][0]["properties"]["cut_off"] is True
    write_site(site, {**entry, "title": "Y"})  # same key replaces, never duplicates
    assert len(json.loads((site / "cities.json").read_text("utf-8"))) == 1


def test_closed_roads_collapse_directions_and_clip_to_water():
    from floodline.stages.reach import closed_roads

    idx = pd.MultiIndex.from_tuples([(1, 2, 0), (2, 1, 0), (3, 4, 0)], names=["u", "v", "key"])
    edges = gpd.GeoDataFrame(
        {"highway": ["primary", "primary", ["residential", "service"]], "name": ["A", "A", np.nan]},
        geometry=[LineString([(0, 0), (100, 0)]), LineString([(100, 0), (0, 0)]), LineString([(0, 50), (100, 50)])],
        index=idx,
        crs="EPSG:32633",
    )
    flood = box(40, -10, 60, 100)
    roads = closed_roads(edges, pd.Series([True, True, True], index=idx), flood)
    assert len(roads) == 2
    assert roads.under_water_m.tolist() == [20.0, 20.0] and roads.segment_m.tolist() == [100.0, 100.0]
    assert roads.highway.tolist() == ["primary", "residential, service"] and roads.name.tolist() == ["A", ""]
