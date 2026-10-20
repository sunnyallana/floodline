"""Stage 4c: what is exposed in each hex: OpenStreetMap buildings in the flood and in cut-off hexes.

A fully flooded meadow and a flooded street of houses have the same water fraction; only the second needs a
boat. This stage counts building footprints per hex, for the hexes that matter to a responder:

- ``buildings_in_flood``: footprints that intersect the mapped flood extent, counted in the hex of their centroid;
- ``buildings_total``: every footprint in hexes that are wet or cut off (from the reach stage).

Building counts stand in for population, which is not available at hex scale for a historical event. OSM is the
current map, not the 2013 one; for central Dresden and Prague the building stock has barely changed.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from floodline import osm
from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)


def target_cells(layer: pd.DataFrame, reach: pd.DataFrame | None = None) -> list[str]:
    """Hexes worth counting: any satellite water, or cut off from the road network."""
    wet = set(layer.h3[layer.satellite_water_fraction.fillna(0) > 0])
    cut = set(reach.h3[reach.cut_off.astype(bool)]) if reach is not None and "cut_off" in reach else set()
    return sorted(wet | cut)


def count_buildings(buildings, flood_geometry, cells: list[str]) -> pd.DataFrame:
    """Per-hex footprint counts. ``buildings`` is a GeoDataFrame in EPSG:4326 (polygon footprints only)."""
    import h3
    from shapely import STRtree
    from shapely.geometry import MultiPolygon

    if not cells:
        return pd.DataFrame(columns=["h3", "buildings_total", "buildings_in_flood"])
    resolution = h3.get_resolution(cells[0])
    pts = buildings.geometry.representative_point()
    b_cells = pd.Series([h3.latlng_to_cell(y, x, resolution) for x, y in zip(pts.x, pts.y, strict=True)])
    parts = list(flood_geometry.geoms) if isinstance(flood_geometry, MultiPolygon) else [flood_geometry]
    tree = STRtree([p for p in parts if not p.is_empty])
    hit, _ = tree.query(buildings.geometry.to_numpy(), predicate="intersects")
    in_flood = np.zeros(len(buildings), dtype=bool)
    in_flood[np.unique(hit)] = True
    df = pd.DataFrame({"h3": b_cells.to_numpy(), "in_flood": in_flood})
    g = df.groupby("h3")
    out = pd.DataFrame({"buildings_total": g.size(), "buildings_in_flood": g.in_flood.sum()}).reset_index()
    targets = set(cells)
    out.loc[~out.h3.isin(targets), "buildings_total"] = np.nan  # only partially queried outside the targets
    out = out[out.h3.isin(targets) | (out.buildings_in_flood > 0)]
    full = pd.DataFrame({"h3": cells}).merge(out, on="h3", how="outer")
    full["buildings_in_flood"] = full.buildings_in_flood.fillna(0).astype(int)
    full.loc[full.h3.isin(targets), "buildings_total"] = full.buildings_total.fillna(0)
    return full.sort_values("h3").reset_index(drop=True)


BUILDING_SOURCES: tuple[str, ...] = ("osm", "microsoft", "max")


def max_counts(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Per-hexagon maximum over several sources' counts (one frame passes through unchanged)."""
    if len(frames) == 1:
        return frames[0]
    merged = pd.concat(frames, ignore_index=True)
    return merged.groupby("h3", as_index=False).agg(
        buildings_total=("buildings_total", "max"), buildings_in_flood=("buildings_in_flood", "max")
    )


def run(
    paths: Paths,
    layer_csv: Path,
    ems_source: Path,
    reach_csv: Path | None = None,
    out_name: str = "exposure.csv",
    source: osm.OsmSource | None = None,
    buildings_source: str = "osm",
) -> pd.DataFrame:
    """``buildings_source``: ``osm``, ``microsoft`` (ML footprints, see ``floodline.buildings``) or ``max``, the
    larger count of the two per hexagon, which fills OSM gaps without double-counting houses both sources map."""
    if buildings_source not in BUILDING_SOURCES:
        raise ValueError(f"buildings_source must be one of {BUILDING_SOURCES}")
    import geopandas as gpd
    from shapely.ops import unary_union

    from floodline.stages.satellite import hex_polygons, load_ems

    manifest = RunManifest(
        "exposure",
        params={
            "layer": str(layer_csv),
            "ems_source": str(ems_source),
            "reach": str(reach_csv),
            "osm": (source or osm.OsmSource()).describe(),
            "buildings_source": buildings_source,
        },
    )
    layer = pd.read_csv(layer_csv)
    reach = pd.read_csv(reach_csv) if reach_csv is not None and reach_csv.exists() else None
    cells = target_cells(layer, reach)
    flood, _, _, _ = load_ems(ems_source)
    area = unary_union(hex_polygons(cells))
    counts, queried = [], {}
    if buildings_source in ("osm", "max"):
        source = source or osm.OsmSource()
        feats = source.features(area, {"building": True})
        feats = feats[feats.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        osm_buildings = gpd.GeoDataFrame(geometry=feats.geometry.to_numpy(), crs="EPSG:4326")
        queried["osm"] = len(osm_buildings)
        counts.append(count_buildings(osm_buildings, flood, cells))
    if buildings_source in ("microsoft", "max"):
        from floodline import buildings as ml_buildings

        ms = ml_buildings.microsoft(area.bounds, paths.data / "external" / "buildings")
        ms = ms[ms.intersects(area)]
        queried["microsoft"] = len(ms)
        counts.append(count_buildings(ms, flood, cells))
    df = max_counts(counts)
    df.to_csv(paths.results / out_name, index=False)
    summary = {
        "target_hexes": len(cells),
        "buildings_queried": queried,
        "buildings_in_flood": int(df.buildings_in_flood.sum()),
        "hexes_with_flooded_buildings": int((df.buildings_in_flood > 0).sum()),
    }
    manifest.finish(**summary).write(paths.results / f"{Path(out_name).stem}_run.json")
    log.info("exposure: %s", summary)
    return df
