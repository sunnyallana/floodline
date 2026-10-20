"""Stage 4b: road reachability per hex, before and during the flood.

Travel time from the nearest staging point (fire stations and hospitals from OpenStreetMap, or points a
coordinator names) to each hex over the OSM drive network, computed twice:

- ``reach_dry_min``: the normal network;
- ``reach_flood_min``: the same network with every road segment that crosses the mapped flood extent removed.

A hex is ``cut_off`` when it has roads, is reachable on the normal network and is not reachable once flooded roads
are removed: those are the places a ground team cannot get to and a boat or helicopter has to. ``delay_min`` is the
detour the flood forces on hexes that stay reachable. Hexes with no drivable road inside them get NaN, not "cut
off": a meadow or the river channel is not isolated, it simply has no road.

Each hex takes the best (minimum) time over the road nodes inside it, so one passable street is enough to count
it as reachable. Bridges over the normal river channel are kept, because the channel is permanent water and not
part of the flood polygons; whether a bridge was closed is not in the data.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from floodline import osm
from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

DEFAULT_SPEED_KMH = 30.0  # conservative urban speed for a loaded rescue vehicle
STAGING_TAGS: dict[str, list[str]] = {"amenity": ["fire_station", "hospital"]}
MAX_SNAP_M = 500.0  # a staging point further than this from any road is dropped
# Length of road inside the flood before the segment counts as closed. Chosen against EMS road damage grading
# for EMSR517 (results/validation_roads.csv): 5 m maximises F1 pooled over 11 towns (precision 0.94, recall 0.85
# against graded roads); 0 m adds 3 points of recall for 12 points of precision lost to boundary grazes, and
# 15 m loses 9 points of recall. Rapid-mapping polygons are placed to about 10-20 m at 1:10,000.
MIN_OVERLAP_M = 5.0


def travel_minutes(graph: nx.MultiDiGraph, sources: Iterable[int], weight: str = "travel_time") -> dict[int, float]:
    """Multi-source Dijkstra: seconds from the nearest source to every reachable node, returned in minutes."""
    sources = [s for s in sources if s in graph]
    if not sources:
        return {}
    dist = nx.multi_source_dijkstra_path_length(graph, set(sources), weight=weight)
    return {n: s / 60.0 for n, s in dist.items()}


def ensure_travel_time(graph: nx.MultiDiGraph, speed_kmh: float = DEFAULT_SPEED_KMH) -> nx.MultiDiGraph:
    """Add a ``travel_time`` (seconds) attribute from edge ``length`` (metres) when the graph lacks one."""
    for _, _, data in graph.edges(data=True):
        if "travel_time" not in data:
            data["travel_time"] = float(data.get("length", 0.0)) / (speed_kmh * 1000.0 / 3600.0)
    return graph


def flooded_edges(edges, flood_geometry, min_overlap_m: float = MIN_OVERLAP_M) -> pd.Series:
    """Boolean per edge row: does at least ``min_overlap_m`` of the edge lie in the flood? Metric CRS for both.

    A road that only touches the polygon boundary is not closed: a street running along the flood edge is how
    responders reach it. See ``MIN_OVERLAP_M`` for how the threshold was chosen.
    """
    from shapely import STRtree
    from shapely.geometry import MultiPolygon

    parts = list(flood_geometry.geoms) if isinstance(flood_geometry, MultiPolygon) else [flood_geometry]
    parts = [p for p in parts if not p.is_empty]
    if not parts:
        return pd.Series(False, index=edges.index)
    tree = STRtree(parts)
    geoms = edges.geometry.to_numpy()
    hit_edges, hit_parts = tree.query(geoms, predicate="intersects")
    overlap = np.zeros(len(edges))
    for e, q in zip(hit_edges, hit_parts, strict=True):
        overlap[e] += geoms[e].intersection(parts[q]).length
    return pd.Series(overlap >= min_overlap_m, index=edges.index)


def _tag(v) -> str:
    """OSM tags can be multi-valued after simplification, e.g. highway=[primary, trunk]."""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)


def closed_roads(edges_m, is_flooded: pd.Series, flood_m):
    """One row per closed road (both directions of a two-way street collapse), geometry = the part under water.

    ``edges_m`` is the OSMnx edge GeoDataFrame (index ``u, v, key``) in a metric CRS.
    """
    import geopandas as gpd

    closed = edges_m[is_flooded.to_numpy()]
    keys = set(closed.index)
    keep = [(u, v, k) for u, v, k in closed.index if u < v or (v, u, k) not in keys]
    closed = closed.loc[keep]
    return gpd.GeoDataFrame(
        {
            "highway": [_tag(h) for h in closed.get("highway", pd.Series(index=closed.index, dtype=object))],
            "name": [_tag(n) for n in closed.get("name", pd.Series(index=closed.index, dtype=object))],
            "segment_m": closed.length.round(0).to_numpy(),
            "under_water_m": closed.geometry.intersection(flood_m).length.round(0).to_numpy(),
        },
        geometry=closed.geometry.intersection(flood_m).to_numpy(),
        crs=edges_m.crs,
    ).reset_index(drop=True)


def without_edges(graph: nx.MultiDiGraph, edge_keys: Iterable[tuple[int, int, int]]) -> nx.MultiDiGraph:
    g = graph.copy()
    g.remove_edges_from(list(edge_keys))
    return g


def snap(points_xy: np.ndarray, node_ids: np.ndarray, node_xy: np.ndarray, max_m: float = MAX_SNAP_M) -> list[int]:
    """Nearest node for each point in a metric CRS; points further than ``max_m`` from every node are dropped."""
    from shapely import STRtree, points

    tree = STRtree(points(node_xy))
    idx, dist = tree.query_nearest(points(points_xy), return_distance=True, all_matches=False)
    return [int(node_ids[j]) for (_, j), d in zip(idx.T, dist, strict=True) if d <= max_m]


def per_cell(node_cells: pd.Series, minutes: dict[int, float], cells: list[str]) -> pd.Series:
    """Best travel time over the road nodes inside each hex; inf if it has nodes but none is reachable."""
    t = node_cells.index.map(lambda n: minutes.get(n, np.inf)).to_numpy(dtype=float)
    best = pd.Series(t, index=node_cells.index).groupby(node_cells).min()
    return best.reindex(cells)  # NaN where the hex has no road node


def reach_table(
    graph: nx.MultiDiGraph, cells: list[str], sources: list[int], flooded: Iterable[tuple[int, int, int]] = ()
) -> pd.DataFrame:
    """Per-hex dry and flooded travel times, detour and cut-off flag. ``graph`` nodes need ``x``/``y`` in lon/lat."""
    import h3

    resolution = h3.get_resolution(cells[0]) if cells else 8
    node_cells = pd.Series(
        {n: h3.latlng_to_cell(d["y"], d["x"], resolution) for n, d in graph.nodes(data=True)}, dtype=object
    )
    flooded = list(flooded)
    dry = per_cell(node_cells, travel_minutes(graph, sources), cells)
    wet_graph = without_edges(graph, flooded)
    wet = per_cell(node_cells, travel_minutes(wet_graph, sources), cells)
    df = pd.DataFrame({"h3": cells, "reach_dry_min": dry.to_numpy(), "reach_flood_min": wet.to_numpy()})
    has_road = df.reach_dry_min.notna()
    df["cut_off"] = has_road & np.isfinite(df.reach_dry_min) & ~np.isfinite(df.reach_flood_min)
    finite = np.isfinite(df.reach_dry_min) & np.isfinite(df.reach_flood_min)
    df["delay_min"] = np.where(finite, df.reach_flood_min - df.reach_dry_min, np.nan)
    for c in ("reach_dry_min", "reach_flood_min", "delay_min"):
        df[c] = df[c].round(1)
    return df


def staging_from_osm(
    polygon, tags: dict[str, list[str]] | None = None, source: osm.OsmSource | None = None
) -> pd.DataFrame:
    """Fire stations and hospitals inside ``polygon`` (EPSG:4326) as representative points."""
    gdf = (source or osm.OsmSource()).features(polygon, tags or STAGING_TAGS)
    if gdf.empty:
        return pd.DataFrame(columns=["name", "kind", "lat", "lon"])
    pts = gdf.geometry.representative_point()
    return pd.DataFrame(
        {
            "name": gdf.get("name", pd.Series(index=gdf.index, dtype=object)).fillna("").to_numpy(),
            "kind": gdf.get("amenity", pd.Series(index=gdf.index, dtype=object)).fillna("").to_numpy(),
            "lat": pts.y.to_numpy(),
            "lon": pts.x.to_numpy(),
        }
    ).drop_duplicates(subset=["lat", "lon"])


def run(
    paths: Paths,
    cells: list[str],
    ems_source: Path,
    staging_latlon: list[tuple[float, float]] | None = None,
    out_stem: str = "reach",
    network_type: str = "drive",
    buffer_m: float = 1500.0,
    source: osm.OsmSource | None = None,
) -> pd.DataFrame:
    """Download the road network around the mapped area, remove flooded roads, and write per-hex reachability.

    Writes ``<stem>.csv`` (per hex), ``<stem>_flooded_roads.geojson`` and ``<stem>_staging.geojson`` for the map,
    and ``<stem>_run.json``.
    """
    import geopandas as gpd
    import osmnx as ox

    from floodline.stages.satellite import load_ems

    manifest = RunManifest(
        "reach",
        params={
            "cells": len(cells),
            "ems_source": str(ems_source),
            "network_type": network_type,
            "buffer_m": buffer_m,
            "min_overlap_m": MIN_OVERLAP_M,
            "osm": (source or osm.OsmSource()).describe(),
            "staging": staging_latlon or "osm:" + ",".join(STAGING_TAGS["amenity"]),
        },
    )
    flood, aoi, _, _ = load_ems(ems_source)
    aoi_gs = gpd.GeoSeries([aoi], crs="EPSG:4326")
    metric = aoi_gs.estimate_utm_crs()
    area = aoi_gs.to_crs(metric).buffer(buffer_m).to_crs("EPSG:4326").iloc[0]

    source = source or osm.OsmSource()
    graph = source.graph(area, network_type)
    graph = ox.add_edge_travel_times(ox.add_edge_speeds(graph))
    nodes, edges = ox.graph_to_gdfs(graph)
    flood_m = gpd.GeoSeries([flood], crs="EPSG:4326").to_crs(metric).iloc[0]
    edges_m = edges.to_crs(metric)
    is_flooded = flooded_edges(edges_m, flood_m)
    flooded_keys = list(edges.index[is_flooded.to_numpy()])

    if staging_latlon:
        staging = pd.DataFrame(staging_latlon, columns=["lat", "lon"]).assign(name="", kind="named")
    else:
        staging = staging_from_osm(area, source=source)
    st = gpd.GeoDataFrame(staging, geometry=gpd.points_from_xy(staging.lon, staging.lat), crs="EPSG:4326")
    st["in_flood"] = st.geometry.to_crs(metric).within(flood_m).to_numpy()
    st = st[~st.in_flood].reset_index(drop=True)  # a station under water stages nothing
    nodes_m = nodes.to_crs(metric)
    node_xy = np.column_stack([nodes_m.geometry.x, nodes_m.geometry.y])
    st_m = st.geometry.to_crs(metric)
    sources = snap(np.column_stack([st_m.x, st_m.y]), nodes.index.to_numpy(), node_xy)
    if not sources:
        raise RuntimeError("no staging point within reach of the road network")

    df = reach_table(graph, cells, sources, flooded_keys)
    df.to_csv(paths.results / f"{out_stem}.csv", index=False)
    roads = closed_roads(edges_m, is_flooded, flood_m).to_crs("EPSG:4326")
    roads.to_file(paths.results / f"{out_stem}_flooded_roads.geojson", driver="GeoJSON")
    st[["name", "kind", "geometry"]].to_file(paths.results / f"{out_stem}_staging.geojson", driver="GeoJSON")

    has_road = df.reach_dry_min.notna()
    summary = {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "flooded_edges": len(flooded_keys),
        "closed_roads": len(roads),
        "road_under_water_km": round(float(roads.to_crs(metric).length.sum() / 1000.0), 2),
        "staging_points": len(sources),
        "hexes": len(df),
        "hexes_with_roads": int(has_road.sum()),
        "hexes_cut_off": int(df.cut_off.sum()),
        "median_dry_min": float(df.reach_dry_min[np.isfinite(df.reach_dry_min)].median()),
        "hexes_delayed_over_5_min": int((df.delay_min > 5).sum()),
    }
    manifest.finish(**summary).write(paths.results / f"{out_stem}_run.json")
    log.info("reach %s: %s", out_stem, summary)
    return df
