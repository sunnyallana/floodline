"""Stage 4b: road reachability per hex.

Travel time from the nearest staging point (a hospital, fire station or relief camp a coordinator names) to each
hex centre over the OpenStreetMap road network. Hexes the network cannot reach get ``inf``; the fusion stage treats
unknown and unreachable alike as isolated. Flooded roads are not removed from the graph yet; when the satellite
layer is present, edges inside the flood extent can be penalised, which is the obvious next step.
"""

from __future__ import annotations

from collections.abc import Iterable

import networkx as nx
import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

DEFAULT_SPEED_KMH = 30.0  # conservative urban speed for a loaded rescue vehicle


def travel_minutes(graph: nx.MultiDiGraph, sources: Iterable[int], weight: str = "travel_time") -> dict[int, float]:
    """Multi-source Dijkstra: seconds from the nearest source to every reachable node, returned in minutes."""
    dist = nx.multi_source_dijkstra_path_length(graph, set(sources), weight=weight)
    return {n: s / 60.0 for n, s in dist.items()}


def ensure_travel_time(graph: nx.MultiDiGraph, speed_kmh: float = DEFAULT_SPEED_KMH) -> nx.MultiDiGraph:
    """Add a ``travel_time`` (seconds) attribute from edge ``length`` (metres) when the graph lacks one."""
    for _, _, data in graph.edges(data=True):
        if "travel_time" not in data:
            data["travel_time"] = float(data.get("length", 0.0)) / (speed_kmh * 1000.0 / 3600.0)
    return graph


def reach_per_cell(graph: nx.MultiDiGraph, cells: list[str], staging_latlon: list[tuple[float, float]]) -> pd.DataFrame:
    import h3
    import osmnx as ox

    sources = [ox.distance.nearest_nodes(graph, lon, lat) for lat, lon in staging_latlon]
    minutes = travel_minutes(ensure_travel_time(graph), sources)
    rows = []
    for cell in cells:
        lat, lon = h3.cell_to_latlng(cell)
        node = ox.distance.nearest_nodes(graph, lon, lat)
        rows.append({"h3": cell, "reach_minutes": round(minutes.get(node, float("inf")), 1)})
    return pd.DataFrame(rows)


def run(
    paths: Paths,
    cells: list[str],
    staging_latlon: list[tuple[float, float]],
    out_name: str = "reach_layer.csv",
    network_type: str = "drive",
) -> pd.DataFrame:
    import h3
    import osmnx as ox

    manifest = RunManifest(
        "reach", params={"cells": len(cells), "staging": staging_latlon, "network_type": network_type}
    )
    lats, lons = zip(*[h3.cell_to_latlng(c) for c in cells], strict=True)
    pad = 0.02
    graph = ox.graph_from_bbox(
        (min(lons) - pad, min(lats) - pad, max(lons) + pad, max(lats) + pad), network_type=network_type
    )
    graph = ox.add_edge_speeds(graph)
    graph = ox.add_edge_travel_times(graph)
    df = reach_per_cell(graph, cells, staging_latlon)
    out = paths.results / out_name
    df.to_csv(out, index=False)
    reachable = df.reach_minutes[df.reach_minutes < float("inf")]
    manifest.finish(
        nodes=graph.number_of_nodes(),
        reachable=len(reachable),
        median_minutes=float(reachable.median()) if len(reachable) else None,
    ).write(paths.results / "reach_run.json")
    log.info(
        "%d hexes, %d reachable, median %.1f min -> %s",
        len(df),
        len(reachable),
        reachable.median() if len(reachable) else -1,
        out,
    )
    return df
