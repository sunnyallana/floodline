import pandas as pd
import pytest

h3 = pytest.importorskip("h3")

from floodline.stages.agreement import agreement  # noqa: E402


def test_own_hex_and_ring_agreement_and_unmapped_photos():
    wet = h3.latlng_to_cell(50.08, 14.42, 8)
    dry_neighbour = next(c for c in h3.grid_ring(wet, 1))
    far_dry = h3.latlng_to_cell(50.30, 14.80, 8)
    outside = h3.latlng_to_cell(48.20, 16.40, 8)
    water = pd.Series({wet: 0.6, dry_neighbour: 0.0, far_dry: 0.0})
    photos = pd.DataFrame(
        {
            "h3": [wet, dry_neighbour, far_dry, outside],
            "rel_flooding": [True, True, False, True],
            "rel_depth": [True, False, False, False],
        }
    )
    _, s = agreement(photos, water, k=1)
    assert s["photos_in_mapped_area"] == 3 and s["photos_outside_mapped_area"] == 1
    assert s["all_mapped"]["own_hex_wet"] == round(1 / 3, 3)
    assert s["all_mapped"]["within_1_ring_wet"] == round(2 / 3, 3)  # the bank photo counts with the ring
    assert s["rel_flooding"]["n"] == 2 and s["rel_flooding"]["within_1_ring_wet"] == 1.0


def test_permanent_water_counts_as_water_but_not_as_flood():
    river_hex = h3.latlng_to_cell(50.086, 14.411, 8)
    water = pd.Series({river_hex: 0.0})
    permanent = pd.Series({river_hex: 0.4})
    photos = pd.DataFrame({"h3": [river_hex], "rel_flooding": [True], "rel_depth": [False]})
    _, s = agreement(photos, water, k=1, permanent=permanent)
    assert s["all_mapped"]["within_1_ring_wet"] == 0.0
    assert s["all_mapped"]["within_1_ring_flood_or_permanent"] == 1.0


def test_distances_separate_river_photos_from_flood_photos(tmp_path):
    gpd = pytest.importorskip("geopandas")
    from shapely.geometry import box

    from floodline.stages.agreement import distances

    pkg = tmp_path / "EMSR045" / "pkg"
    pkg.mkdir(parents=True)
    gpd.GeoDataFrame(geometry=[box(14.30, 50.00, 14.32, 50.02)], crs="EPSG:4326").to_file(pkg / "X_Crisis_event_A.shp")
    gpd.GeoDataFrame(geometry=[box(14.40, 50.00, 14.41, 50.10)], crs="EPSG:4326").to_file(pkg / "X_Hydrology_A_LAF.shp")
    gpd.GeoDataFrame(geometry=[box(14.25, 49.95, 14.45, 50.15)], crs="EPSG:4326").to_file(pkg / "X_AreaOfInterest.shp")
    # one photo in the flood, two copies of one geotag on the river, one outside the mapped area
    photos = pd.DataFrame({"lat": [50.01, 50.05, 50.05, 51.0], "lon": [14.31, 14.405, 14.405, 14.4]})
    out, s = distances(photos, [tmp_path / "EMSR045"])
    per_photo, per_loc = s["EMSR045"]["per_photo"], s["EMSR045"]["per_unique_location"]
    assert len(out) == 3 and per_photo["n"] == 3 and per_loc["n"] == 2
    assert per_photo["within_50m_flood"] == round(1 / 3, 3) and per_loc["within_50m_flood"] == 0.5
    assert per_photo["within_50m_flood_or_permanent"] == 1.0
