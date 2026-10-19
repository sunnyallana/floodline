"""Stage 4: satellite flood extent per H3 hexagon.

Reference extent comes from Copernicus Emergency Management Service rapid-mapping products: each activation ships
vector packages whose ``observedEventA`` (area) layer is the mapped flood extent at the sensing time. This stage
reads one or more of those layers, dissolves them, and reports the flooded fraction of every hex that intersects
the area of interest. The Sentinel-1 water mask from a foundation model is a planned second source and would feed
the same per-hex fraction.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from floodline.config import Paths
from floodline.location import H3_RESOLUTION
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)


def hex_polygons(cells: list[str]):
    import h3
    from shapely.geometry import Polygon

    return [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in cells]


def cells_covering(geometry, resolution: int = H3_RESOLUTION) -> list[str]:
    """All H3 cells whose centre falls inside the geometry (h3 v4 polygon fill), in lon/lat order of the input."""
    import h3
    from shapely.geometry import MultiPolygon, Polygon

    polys = list(geometry.geoms) if isinstance(geometry, MultiPolygon) else [geometry]
    cells: set[str] = set()
    for poly in polys:
        if not isinstance(poly, Polygon) or poly.is_empty:
            continue
        outer = [(lat, lng) for lng, lat in poly.exterior.coords]
        holes = [[(lat, lng) for lng, lat in ring.coords] for ring in poly.interiors]
        cells |= set(h3.polygon_to_cells(h3.LatLngPoly(outer, *holes), resolution))
    return sorted(cells)


def water_fraction_per_cell(flood_geometry, cells: list[str]) -> pd.DataFrame:
    """Fraction of each hex covered by the flood geometry (planar on lon/lat; fine at hex scale)."""
    polys = hex_polygons(cells)
    rows = []
    for cell, poly in zip(cells, polys, strict=True):
        inter = poly.intersection(flood_geometry)
        rows.append({"h3": cell, "satellite_water_fraction": round(inter.area / poly.area, 4) if poly.area else 0.0})
    return pd.DataFrame(rows)


def load_ems_extent(paths_or_dir: list[Path] | Path, layer_hint: str = "observedEventA"):
    """Read EMS observed-event area layers (shapefile or GeoPackage) and dissolve them into one geometry.

    ``paths_or_dir`` may be a directory (searched recursively for files whose name contains ``layer_hint``) or an
    explicit list of vector files. Returns a shapely geometry in EPSG:4326.
    """
    import geopandas as gpd

    if isinstance(paths_or_dir, Path):
        files = [
            p
            for p in paths_or_dir.rglob("*")
            if layer_hint.lower() in p.name.lower() and p.suffix.lower() in {".shp", ".gpkg", ".json", ".geojson"}
        ]
    else:
        files = list(paths_or_dir)
    if not files:
        raise FileNotFoundError(f"no EMS layer matching '{layer_hint}' under {paths_or_dir}")
    frames = [gpd.read_file(f).to_crs("EPSG:4326") for f in files]
    gdf = pd.concat(frames, ignore_index=True)
    geom = (
        gdf.geometry.buffer(0).union_all() if hasattr(gdf.geometry, "union_all") else gdf.geometry.buffer(0).unary_union
    )
    log.info("EMS extent from %d file(s): %.1f km²", len(files), geom.area * 111.32 * 111.32)
    return geom


def run(
    paths: Paths, ems_source: Path, out_name: str = "satellite_layer.csv", aoi_cells: list[str] | None = None
) -> pd.DataFrame:
    manifest = RunManifest("satellite", params={"ems_source": str(ems_source), "resolution": H3_RESOLUTION})
    geom = load_ems_extent(ems_source)
    cells = aoi_cells or cells_covering(geom.buffer(0.02))  # ~2 km margin so dry hexes around the extent get 0
    df = water_fraction_per_cell(geom, cells)
    out = paths.results / out_name
    df.to_csv(out, index=False)
    manifest.finish(cells=len(df), flooded_cells=int((df.satellite_water_fraction > 0).sum())).write(
        paths.results / "satellite_run.json"
    )
    log.info("%d hexes, %d with water -> %s", len(df), (df.satellite_water_fraction > 0).sum(), out)
    return df
