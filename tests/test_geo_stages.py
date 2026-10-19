import json

import pandas as pd
import pytest

h3 = pytest.importorskip("h3")
shapely = pytest.importorskip("shapely")
nx = pytest.importorskip("networkx")
from shapely.geometry import Polygon, box  # noqa: E402

from floodline.stages.reach import ensure_travel_time, travel_minutes  # noqa: E402
from floodline.stages.satellite import cells_covering, load_ems, water_fraction_per_cell  # noqa: E402
from floodline.stages.serve import write_geojson  # noqa: E402


def test_cells_covering_and_water_fraction():
    flood = box(67.00, 24.85, 67.03, 24.87)  # ~3 km × 2 km block in Karachi
    cells = cells_covering(flood)
    assert 5 <= len(cells) <= 40
    df = water_fraction_per_cell(flood, cells)
    assert df.satellite_water_fraction.between(0, 1).all()
    assert df.satellite_water_fraction.max() == pytest.approx(1.0, abs=1e-3)  # interior hexes fully flooded
    dry = water_fraction_per_cell(flood, [h3.latlng_to_cell(24.95, 67.10, 8)])
    assert dry.satellite_water_fraction.iloc[0] == 0.0


def test_holes_are_respected():
    outer = box(67.00, 24.85, 67.06, 24.90)
    hole = box(67.02, 24.87, 67.04, 24.88)
    ring = Polygon(outer.exterior.coords, [hole.exterior.coords])
    inside_hole = h3.latlng_to_cell(24.875, 67.03, 9)
    assert inside_hole not in cells_covering(ring, resolution=9)


def test_travel_minutes_multi_source_and_unreachable():
    g = nx.MultiDiGraph()
    g.add_edge(1, 2, length=1000.0)
    g.add_edge(2, 3, length=2000.0)
    g.add_edge(4, 5, length=500.0)  # disconnected component
    ensure_travel_time(g, speed_kmh=60.0)
    minutes = travel_minutes(g, sources=[1])
    assert minutes[1] == 0.0 and minutes[2] == pytest.approx(1.0) and minutes[3] == pytest.approx(3.0)
    assert 4 not in minutes and 5 not in minutes


def test_geojson_export_roundtrip(tmp_path):
    cell = h3.latlng_to_cell(24.86, 67.0, 8)
    hexes = pd.DataFrame([{"h3": cell, "priority": 0.7, "confidence": 0.5, "n_reports": 2, "severity_max": 0.9}])
    out = write_geojson(hexes, tmp_path / "hexes.geojson")
    fc = json.loads(out.read_text(encoding="utf-8"))
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 1
    f = fc["features"][0]
    assert f["geometry"]["type"] == "Polygon" and f["properties"]["priority"] == 0.7


def test_multipart_flood_and_aoi_layers(tmp_path):
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import MultiPolygon

    pkg = tmp_path / "EMSR999" / "EMSR999_AOI01_DEL_PRODUCT_v1_vector"
    pkg.mkdir(parents=True)
    flood = MultiPolygon([box(67.00, 24.85, 67.01, 24.86), box(67.03, 24.85, 67.04, 24.86)])
    aoi = box(66.99, 24.84, 67.05, 24.87)
    gpd.GeoDataFrame(geometry=[flood], crs="EPSG:4326").to_file(pkg / "EMSR999_AOI01_DEL_PRODUCT_observedEventA_v1.shp")
    gpd.GeoDataFrame(geometry=[aoi], crs="EPSG:4326").to_file(pkg / "EMSR999_AOI01_DEL_PRODUCT_areaOfInterestA_v1.shp")
    gpd.GeoDataFrame(geometry=[flood], crs="EPSG:4326").to_file(
        pkg / "EMSR999_AOI01_DEL_PRODUCT_observedEventA_v1.json", driver="GeoJSON"
    )  # same layer shipped twice, as the real packages do
    f, a, _permanent, info = load_ems(tmp_path / "EMSR999")
    assert info["flood_layers"] == 1 and info["aoi_layers"] == 1 and info["aoi_km2"] > info["flood_km2"] > 0
    df = water_fraction_per_cell(f, cells_covering(a))
    assert (df.satellite_water_fraction == 0).any() and (df.satellite_water_fraction > 0).any()


def test_2013_schema_layer_names(tmp_path):
    gpd = pytest.importorskip("geopandas")

    pkg = tmp_path / "EMSR045" / "EMSR045_06PRAHA_DELINEATION_DETAIL01_v2_vector"
    pkg.mkdir(parents=True)
    gpd.GeoDataFrame(geometry=[box(14.40, 50.05, 14.42, 50.07)], crs="EPSG:4326").to_file(
        pkg / "EMSR-045_06Prague_20130603_CZE_FL_v2_Crisis_event_A.shp"
    )
    gpd.GeoDataFrame(geometry=[box(14.35, 50.00, 14.50, 50.12)], crs="EPSG:4326").to_file(
        pkg / "EMSR-045_06Prague_20130603_CZE_FL_v2_AreaOfInterest.shp"
    )
    gpd.GeoDataFrame(geometry=[box(14.41, 50.04, 14.415, 50.08)], crs="EPSG:4326").to_file(
        pkg / "EMSR-045_06Prague_20130603_CZE_FL_v2_Hydrology_A_LAF.shp"
    )
    _, _, permanent, info = load_ems(tmp_path / "EMSR045")
    assert info["permanent_water_layers"] == 1 and permanent is not None
    assert info["flood_layers"] == 1 and info["aoi_layers"] == 1
