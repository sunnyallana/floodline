"""Stage 5: fuse located reports on H3 hexagons and rank them.

Input: one row per report with ``lat``, ``lon``, ``depth_class``, ``people_visible``, ``people_in_water``,
``people_on_roofs_or_elevated``, ``confidence`` (assessment), ``location_confidence`` and ``captured_at``.
Optional per-hex layers joined by cell id: ``satellite_water_fraction`` (0..1) and ``reach_minutes`` (travel
time from the nearest staging point). Both default to "unknown" and lower the confidence rather than the score.

Output: one row per hex with the evidence summary and a priority score, written as CSV and, when geopandas is
available, GeoParquet with the hex polygon.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from floodline.config import Paths
from floodline.location import H3_RESOLUTION
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

DEPTH_SEVERITY: dict[str, float] = {
    "dry": 0.0,
    "ankle": 0.15,
    "knee": 0.4,
    "waist": 0.7,
    "chest": 0.9,
    "above_head": 1.0,
    "unknown": np.nan,
}
REACH_SATURATION_MIN = 90.0  # travel times at or beyond this count as fully isolated


@dataclass(frozen=True)
class Weights:
    people: float = 0.35
    depth: float = 0.35
    isolation: float = 0.15
    recency: float = 0.15


def to_cells(df: pd.DataFrame, resolution: int = H3_RESOLUTION) -> pd.Series:
    import h3

    return pd.Series([h3.latlng_to_cell(a, b, resolution) for a, b in zip(df.lat, df.lon, strict=True)], index=df.index)


def aggregate(reports: pd.DataFrame, now: pd.Timestamp | None = None, resolution: int = H3_RESOLUTION) -> pd.DataFrame:
    """Per-hex evidence summary from located reports."""
    r = reports.dropna(subset=["lat", "lon"]).copy()
    if r.empty:
        return pd.DataFrame(columns=["h3", "n_reports"])
    r["h3"] = to_cells(r, resolution)
    r["severity"] = r.depth_class.map(DEPTH_SEVERITY)
    r["captured_at"] = pd.to_datetime(r.get("captured_at"), errors="coerce", utc=True)
    now = now or pd.Timestamp.now(tz="UTC")
    g = r.groupby("h3")
    out = pd.DataFrame(
        {
            "n_reports": g.size(),
            "n_with_depth": g.severity.count(),
            "severity_median": g.severity.median(),
            "severity_max": g.severity.max(),
            "people_visible": g.people_visible.sum(min_count=1),
            "people_in_water": g.people_in_water.sum(min_count=1),
            "people_elevated": g.people_on_roofs_or_elevated.sum(min_count=1),
            "assessment_confidence": g.confidence.mean(),
            "location_confidence": g.location_confidence.mean(),
            "first_report": g.captured_at.min(),
            "last_report": g.captured_at.max(),
        }
    ).reset_index()
    out["hours_since_last"] = (now - out.last_report).dt.total_seconds() / 3600.0
    return out


def score(hexes: pd.DataFrame, weights: Weights | None = None) -> pd.DataFrame:
    """Priority in [0, 1] plus a confidence that falls when evidence is thin or layers are missing."""
    weights = weights or Weights()
    h = hexes.copy()
    people = h[["people_in_water", "people_elevated", "people_visible"]].fillna(0)
    people_term = np.tanh(
        (2.0 * people.people_in_water + 2.0 * people.people_elevated + 0.5 * people.people_visible) / 10.0
    )
    depth_term = h.severity_max.fillna(h.get("satellite_water_fraction", pd.Series(np.nan, index=h.index)))
    depth_term = depth_term.fillna(0.0)
    reach = h.get("reach_minutes", pd.Series(np.nan, index=h.index))
    isolation_term = (reach.clip(upper=REACH_SATURATION_MIN) / REACH_SATURATION_MIN).fillna(0.5)
    recency_term = np.exp(-h.hours_since_last.fillna(48.0) / 24.0)
    h["priority"] = (
        weights.people * people_term
        + weights.depth * depth_term
        + weights.isolation * isolation_term
        + weights.recency * recency_term
    ).clip(0, 1)
    evidence = np.tanh(h.n_reports.fillna(0) / 3.0)
    layers_known = (~reach.isna()).astype(float) * 0.5 + (
        ~h.get("satellite_water_fraction", pd.Series(np.nan, index=h.index)).isna()
    ).astype(float) * 0.5
    h["confidence"] = (
        0.5 * h.assessment_confidence.fillna(0.3) * h.location_confidence.fillna(0.0)
        + 0.3 * evidence
        + 0.2 * layers_known
    ).clip(0, 1)
    if "satellite_water_fraction" in h:
        h["photo_satellite_agreement"] = 1.0 - (h.severity_max.fillna(0) - h.satellite_water_fraction).abs()
    return h.sort_values("priority", ascending=False).reset_index(drop=True)


def hex_geometry(cells: pd.Series):
    import h3
    from shapely.geometry import Polygon

    return [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in cells]


def run(paths: Paths, reports_csv: Path, layers_csv: Path | None = None, out_stem: str = "hexes") -> pd.DataFrame:
    reports = pd.read_csv(reports_csv)
    manifest = RunManifest(
        "fuse", params={"reports": str(reports_csv), "layers": str(layers_csv), "resolution": H3_RESOLUTION}
    )
    hexes = aggregate(reports)
    if layers_csv is not None and layers_csv.exists():
        hexes = hexes.merge(pd.read_csv(layers_csv), on="h3", how="left")
    ranked = score(hexes)
    csv = paths.results / f"{out_stem}.csv"
    ranked.to_csv(csv, index=False)
    try:
        import geopandas as gpd

        gdf = gpd.GeoDataFrame(ranked, geometry=hex_geometry(ranked.h3), crs="EPSG:4326")
        gdf.to_parquet(paths.results / f"{out_stem}.parquet")
    except ImportError:
        log.info("geopandas not installed; wrote CSV only")
    manifest.finish(reports=len(reports), hexes=len(ranked)).write(paths.results / "fuse_run.json")
    log.info("%d reports -> %d hexes -> %s", len(reports), len(ranked), csv)
    return ranked
