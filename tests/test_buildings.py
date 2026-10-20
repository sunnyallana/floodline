import gzip
import json

import pandas as pd
import pytest

from floodline import buildings
from floodline.stages.exposure import max_counts


def test_quadkey_matches_the_bing_maps_reference():
    # Bing Maps tile system documentation: tile (3, 5) at level 3 is quadkey "213".
    assert buildings.quadkey(3, 5, 3) == "213"
    assert buildings.tile_xy(0.0, 0.0, 1) == (1, 1)


def test_quadkeys_cover_the_box():
    qs = buildings.quadkeys((68.07, 27.46, 68.44, 27.70))
    assert qs == ["123120220", "123120221", "123120222", "123120223"]  # the Larkana study area spans four tiles
    assert all(len(q) == buildings.ZOOM for q in qs)


def test_read_geojsonl_keeps_footprints_whose_centroid_is_inside(tmp_path):
    pytest.importorskip("geopandas")

    def square(x, y):
        return {
            "type": "Polygon",
            "coordinates": [[[x, y], [x + 0.001, y], [x + 0.001, y + 0.001], [x, y + 0.001], [x, y]]],
        }

    path = tmp_path / "tile.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for x, y in ((68.10, 27.50), (68.20, 27.60), (69.00, 28.00)):
            f.write(json.dumps({"type": "Feature", "geometry": square(x, y), "properties": {"height": -1}}) + "\n")
    gdf = buildings.read_geojsonl(path, (68.0, 27.4, 68.5, 27.7))
    assert len(gdf) == 2 and gdf.crs.to_epsg() == 4326


def test_max_counts_takes_the_better_source_per_hexagon():
    osm = pd.DataFrame({"h3": ["a", "b"], "buildings_total": [40, 2], "buildings_in_flood": [30, 1]})
    ml = pd.DataFrame({"h3": ["a", "b", "c"], "buildings_total": [35, 60, 5], "buildings_in_flood": [25, 50, 0]})
    out = max_counts([osm, ml]).set_index("h3")
    assert out.loc["a"].tolist() == [40, 30] and out.loc["b"].tolist() == [60, 50] and out.loc["c"].tolist() == [5, 0]
    assert max_counts([osm]) is osm
