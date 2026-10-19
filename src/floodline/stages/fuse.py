"""Stage 5: fuse the per-hex layers and located photo reports, and rank hexes for a responder.

Every hex in the mapped area is scored, not only hexes with photos: satellite extent is the coverage backbone and
photos are evidence where they exist. Layers are CSVs keyed by ``h3`` and joined onto the first one:

- satellite: ``satellite_water_fraction``, ``permanent_water_fraction``;
- reach: ``reach_dry_min``, ``reach_flood_min``, ``delay_min``, ``cut_off``;
- exposure: ``buildings_in_flood``, ``buildings_total``.

Reports (optional) need ``lat`` and ``lon``; assessment columns (``depth_class``, ``people_*``, ``confidence``) are
used when present, so located but not yet assessed photos still count as corroboration.

Priority, in [0, 1], is zero unless the hex is wet or cut off. Otherwise it is a weighted sum of
exposure (buildings and people affected), hazard (photo depth if known, else satellite water fraction) and access
(cut off, or the detour the flood forces). Confidence is separate and rises with the number of independent layers
and corroborating photos; a high priority with low confidence is a hex to verify first, not to ignore.
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
EXPOSURE_SCALE = 20.0  # buildings-equivalent at which exposure reaches 1 - 1/e
PERSON_WEIGHT = 5.0  # one person seen in water or on a roof counts as five buildings
DELAY_SATURATION_MIN = 30.0  # a detour this long counts as fully obstructed access


@dataclass(frozen=True)
class Weights:
    exposure: float = 0.45
    hazard: float = 0.30
    access: float = 0.25


def to_cells(df: pd.DataFrame, resolution: int = H3_RESOLUTION) -> pd.Series:
    import h3

    return pd.Series([h3.latlng_to_cell(a, b, resolution) for a, b in zip(df.lat, df.lon, strict=True)], index=df.index)


def _col(df: pd.DataFrame, name: str, default=np.nan) -> pd.Series:
    return df[name] if name in df else pd.Series(default, index=df.index)


def aggregate(reports: pd.DataFrame, resolution: int = H3_RESOLUTION) -> pd.DataFrame:
    """Per-hex evidence summary from located reports. Missing assessment columns become NaN, not errors."""
    r = reports.dropna(subset=["lat", "lon"]).copy()
    if r.empty:
        return pd.DataFrame(columns=["h3", "n_reports"])
    r["h3"] = to_cells(r, resolution)
    r["severity"] = _col(r, "depth_class").map(DEPTH_SEVERITY)
    r["captured_at"] = pd.to_datetime(_col(r, "captured_at", None), errors="coerce", utc=True)
    for c in ("people_visible", "people_in_water", "people_on_roofs_or_elevated", "confidence"):
        r[c] = pd.to_numeric(_col(r, c), errors="coerce")
    r["location_confidence"] = pd.to_numeric(_col(r, "location_confidence"), errors="coerce")
    g = r.groupby("h3")
    return pd.DataFrame(
        {
            "n_reports": g.size(),
            "n_with_depth": g.severity.count(),
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


def score(hexes: pd.DataFrame, weights: Weights | None = None) -> pd.DataFrame:
    """Add ``exposure``, ``hazard``, ``access``, ``priority`` and ``confidence`` columns; sort by priority."""
    w = weights or Weights()
    h = hexes.copy()
    sat = _col(h, "satellite_water_fraction")
    severity = _col(h, "severity_max")
    cut_off = _col(h, "cut_off", False).fillna(False).astype(bool)
    in_flood = _col(h, "buildings_in_flood", 0).fillna(0)
    total = _col(h, "buildings_total", 0).fillna(0)
    people = _col(h, "people_in_water", 0).fillna(0) + _col(h, "people_elevated", 0).fillna(0)

    affected = in_flood + np.where(cut_off, np.maximum(total - in_flood, 0), 0) + PERSON_WEIGHT * people
    h["buildings_affected"] = (in_flood + np.where(cut_off, np.maximum(total - in_flood, 0), 0)).astype(int)
    h["exposure"] = 1.0 - np.exp(-affected / EXPOSURE_SCALE)
    h["hazard"] = np.fmax(severity.to_numpy(dtype=float), sat.to_numpy(dtype=float))
    h["hazard"] = h.hazard.fillna(0.0).clip(0, 1)
    delay = _col(h, "delay_min").fillna(0).clip(lower=0)
    h["access"] = np.where(cut_off, 1.0, (delay / DELAY_SATURATION_MIN).clip(upper=1.0))
    gate = (h.hazard > 0) | cut_off
    h["priority"] = (gate * (w.exposure * h.exposure + w.hazard * h.hazard + w.access * h.access)).clip(0, 1)

    # One third per independent layer: satellite (per hex, may be unmapped), reach and exposure (per run).
    layers_known = (sat.notna().astype(float) + float("reach_dry_min" in h) + float("buildings_in_flood" in h)) / 3.0
    n_reports = _col(h, "n_reports", 0).fillna(0)
    photo = np.tanh(n_reports / 2.0) * _col(h, "location_confidence", 0).fillna(0)
    h["confidence"] = (0.6 * layers_known + 0.4 * photo).clip(0, 1)
    if "severity_max" in h:
        h["photo_satellite_agreement"] = np.where(severity.notna() & sat.notna(), 1.0 - (severity - sat).abs(), np.nan)
    for c in ("exposure", "hazard", "access", "priority", "confidence"):
        h[c] = h[c].round(3)
    return h.sort_values("priority", ascending=False).reset_index(drop=True)


def hex_geometry(cells: pd.Series):
    import h3
    from shapely.geometry import Polygon

    return [Polygon([(lng, lat) for lat, lng in h3.cell_to_boundary(c)]) for c in cells]


def combine(layers: list[pd.DataFrame], reports: pd.DataFrame | None = None) -> pd.DataFrame:
    """Join layers on ``h3`` onto the first (the hex universe); aggregate reports at the same resolution."""
    import h3

    base = layers[0].copy()
    for extra in layers[1:]:
        extra = extra.drop(columns=[c for c in extra.columns if c in base.columns and c != "h3"])
        base = base.merge(extra, on="h3", how="left")
    if reports is not None and len(reports) and len(base):
        rep = aggregate(reports, h3.get_resolution(base.h3.iloc[0]))
        base = base.merge(rep, on="h3", how="left")
        base["n_reports"] = base.n_reports.fillna(0).astype(int)
    return base


def run(paths: Paths, layer_csvs: list[Path], reports_csv: Path | None = None, out_stem: str = "hexes") -> pd.DataFrame:
    manifest = RunManifest(
        "fuse", params={"layers": [str(p) for p in layer_csvs], "reports": str(reports_csv) if reports_csv else None}
    )
    reports = pd.read_csv(reports_csv) if reports_csv is not None else None
    ranked = score(combine([pd.read_csv(p) for p in layer_csvs], reports))
    csv = paths.results / f"{out_stem}.csv"
    ranked.to_csv(csv, index=False)
    try:
        import geopandas as gpd

        gdf = gpd.GeoDataFrame(ranked, geometry=hex_geometry(ranked.h3), crs="EPSG:4326")
        gdf.to_parquet(paths.results / f"{out_stem}.parquet")
    except ImportError:
        log.info("geopandas not installed; wrote CSV only")
    live = ranked[ranked.priority > 0]
    summary = {
        "hexes": len(ranked),
        "hexes_with_priority": len(live),
        "hexes_with_reports": int((ranked.get("n_reports", pd.Series(0)) > 0).sum()),
        "top10": live.head(10)[["h3", "priority", "confidence", "buildings_affected"]].to_dict("records"),
    }
    manifest.finish(**summary).write(paths.results / f"{out_stem}_run.json")
    log.info("fuse %s: %d hexes, %d with priority > 0 -> %s", out_stem, len(ranked), len(live), csv)
    return ranked
