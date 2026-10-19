"""Stage 4: satellite flood extent per H3 hexagon.

Reference extent comes from Copernicus Emergency Management Service rapid-mapping products. Each activation ships
vector packages per area of interest (AOI) with the mapped flood extent at the sensing time. Two schemas exist:

- 2019 onwards: ``*_observedEventA*`` (flood extent) and ``*_areaOfInterestA*`` (mapped area);
- 2013-era:     ``*_Crisis_event_A*`` (flood extent) and ``*_AreaOfInterest*`` (mapped area).

Permanent water (rivers and lakes at normal level: ``*_hydrographyA*`` / ``*_Hydrology_A*``) is reported in its own
column. "Water is visible here" and "this place is flooded" are different questions: in Prague 2013 the Vltava rose
inside its embankments, so photos of a high river sit metres from the channel and kilometres from any overflow.

The AOI decides which hexes get a value at all. A hex inside an AOI but outside the flood polygons is a real 0
(mapped, dry); a hex outside every AOI is absent from the output, i.e. unknown, because nobody mapped it. Without
this distinction unmapped areas would be read as dry.

The Sentinel-1 water mask from a foundation model is a planned second source and would feed the same column.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from floodline.config import Paths
from floodline.location import H3_RESOLUTION
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

FLOOD_LAYER_HINTS: tuple[str, ...] = ("observedeventa", "crisis_event_a")
AOI_LAYER_HINTS: tuple[str, ...] = ("areaofinteresta", "areaofinterest")
PERMANENT_WATER_HINTS: tuple[str, ...] = ("hydrographya", "hydrology_a")  # area layers only, not *L lines
VECTOR_SUFFIXES = {".shp", ".gpkg", ".json", ".geojson"}
EQUAL_AREA_CRS = "EPSG:6933"


def hex_polygons(cells: list[str]):
    import h3
    from shapely.geometry import Polygon

    return [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in cells]


def cells_covering(geometry, resolution: int = H3_RESOLUTION) -> list[str]:
    """All H3 cells whose centre falls inside the geometry (h3 v4 polygon fill)."""
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
    """Fraction of each hex covered by the flood geometry (planar on lon/lat; fine at hex scale).

    The flood geometry is split into its parts and indexed in an STRtree, so each hex is intersected only with the
    few parts that touch it. Intersecting every hex with one dissolved multipolygon is quadratic in practice and
    does not finish for activations with thousands of polygons.
    """
    from shapely import STRtree
    from shapely.geometry import MultiPolygon

    parts = list(flood_geometry.geoms) if isinstance(flood_geometry, MultiPolygon) else [flood_geometry]
    parts = [p for p in parts if not p.is_empty]
    tree = STRtree(parts)
    fractions: list[float] = []
    for poly in hex_polygons(cells):
        hits = tree.query(poly, predicate="intersects")
        if len(hits) == 0 or poly.area == 0:
            fractions.append(0.0)
            continue
        inter = sum(poly.intersection(parts[i]).area for i in hits)
        fractions.append(round(min(inter / poly.area, 1.0), 4))
    return pd.DataFrame({"h3": cells, "satellite_water_fraction": fractions})


_FORMAT_PREFERENCE = {".gpkg": 0, ".shp": 1, ".geojson": 2, ".json": 3}


def _find_layers(root: Path, hints: tuple[str, ...]) -> list[Path]:
    """Matching vector layers, one file per layer: EMS packages ship each layer as both .shp and .json."""
    best: dict[Path, Path] = {}
    for p in root.rglob("*"):
        if p.suffix.lower() not in VECTOR_SUFFIXES:
            continue
        if not any(h in p.name.lower().replace("-", "_") for h in hints):
            continue
        key = p.with_suffix("")
        if key not in best or _FORMAT_PREFERENCE[p.suffix.lower()] < _FORMAT_PREFERENCE[best[key].suffix.lower()]:
            best[key] = p
    return sorted(best.values())


def _dissolve(files: list[Path]):
    import geopandas as gpd

    gdf = pd.concat([gpd.read_file(f).to_crs("EPSG:4326") for f in files], ignore_index=True)
    geoms = gdf.geometry.buffer(0)
    geom = geoms.union_all() if hasattr(geoms, "union_all") else geoms.unary_union
    return geom, gpd.GeoSeries([geom], crs="EPSG:4326").to_crs(EQUAL_AREA_CRS).area.iloc[0] / 1e6


def load_ems(root: Path):
    """Flood extent and mapped AOI for every package under ``root``. Returns (flood, aoi, info) in EPSG:4326.

    The AOI layer of the "observedEventA" schema is matched first; ``AreaOfInterest`` alone would also match it.
    """
    flood_files = _find_layers(root, FLOOD_LAYER_HINTS)
    if not flood_files:
        raise FileNotFoundError(f"no EMS flood-extent layer ({', '.join(FLOOD_LAYER_HINTS)}) under {root}")
    aoi_files = _find_layers(root, AOI_LAYER_HINTS)
    if not aoi_files:
        raise FileNotFoundError(f"no EMS area-of-interest layer under {root}")
    flood, flood_km2 = _dissolve(flood_files)
    aoi, aoi_km2 = _dissolve(aoi_files)
    water_files = _find_layers(root, PERMANENT_WATER_HINTS)
    permanent, permanent_km2 = _dissolve(water_files) if water_files else (None, 0.0)
    info = {
        "flood_layers": len(flood_files),
        "aoi_layers": len(aoi_files),
        "permanent_water_layers": len(water_files),
        "flood_km2": round(float(flood_km2), 1),
        "aoi_km2": round(float(aoi_km2), 1),
        "permanent_water_km2": round(float(permanent_km2), 1),
    }
    log.info("EMS %s: %s", root.name, info)
    return flood, aoi, permanent, info


def run(
    paths: Paths, ems_source: Path, out_name: str = "satellite_layer.csv", resolution: int = H3_RESOLUTION
) -> pd.DataFrame:
    """Res 8 (0.74 km² hexes) suits province-scale activations; res 9 (0.11 km²) suits a city package."""
    manifest = RunManifest("satellite", params={"ems_source": str(ems_source), "resolution": resolution})
    flood, aoi, permanent, info = load_ems(ems_source)
    cells = cells_covering(aoi, resolution)
    df = water_fraction_per_cell(flood, cells)
    df["permanent_water_fraction"] = (
        water_fraction_per_cell(permanent, cells).satellite_water_fraction.to_numpy()
        if permanent is not None
        else float("nan")
    )
    df["ems_activation"] = ems_source.name
    out = paths.results / out_name
    df.to_csv(out, index=False)
    flooded = int((df.satellite_water_fraction > 0).sum())
    manifest.finish(cells=len(df), flooded_cells=flooded, **info).write(
        paths.results / f"satellite_run_{Path(out_name).stem.removeprefix('satellite_')}.json"
    )
    log.info("%s: %d hexes in mapped area, %d with water -> %s", ems_source.name, len(df), flooded, out)
    return df
