"""Validation against Copernicus EMS damage grading: is the ranking right, and is the road rule right?

EMS grading products (activation EMSR517, western Germany, July 2021) map, from post-event imagery, a damage
grade for every affected building point and road segment: "Possibly damaged", "Damaged", "Destroyed" (roads also
"No visible damage" and "Not Analysed"). They are made by image analysts and do not use OpenStreetMap, our
road routing or our priority, so they are an independent reference, with one shared input: the same product's
flood polygons, which this stage also uses as the flood extent.

Two tests:

1. **Road closure rule.** For every analysed EMS road segment, predict "closed" when at least ``t`` metres of
   it lie inside the flood polygons, and compare with the EMS grade, for ``t`` from 0 to 100 m. This needs no
   OSM matching and directly tests the overlap rule used by ``reach``.
2. **Priority ranking.** Run satellite -> reach -> exposure -> fuse on each graded town and measure how much of
   the graded building damage falls in the top-ranked hexagons, against single-layer baselines.

The primary target is "Damaged" + "Destroyed". "Possibly damaged" is reported too, but it is assigned largely
to buildings and roads inside the flood trace, so agreement with it is close to mechanical.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

SEVERE = ("Damaged", "Destroyed")
AFFECTED = ("Possibly damaged", *SEVERE)
UNKNOWN_GRADES = ("Not Analysed", "Not analysed", "Not Applicable")
THRESHOLDS_M: tuple[float, ...] = (0.0, 5.0, 10.0, 15.0, 25.0, 50.0, 100.0)
CAPTURE_FRACTIONS: tuple[float, ...] = (0.05, 0.10, 0.20)
RANKING_SCORES: tuple[str, ...] = ("priority", "satellite_water_fraction", "buildings_in_flood", "hazard")
MIN_SEVERE_BUILDINGS = 20  # towns with fewer graded damaged/destroyed buildings give too few positives to rank


def _layer(root: Path, name: str):
    import geopandas as gpd

    files = sorted(root.glob(f"*{name}*.shp"))
    return gpd.read_file(files[0]) if files else None


def overlap_lengths(lines, flood_geometry) -> np.ndarray:
    """Metres of each line inside the flood geometry. Both in the same metric CRS."""
    from shapely import STRtree
    from shapely.geometry import MultiPolygon

    parts = list(flood_geometry.geoms) if isinstance(flood_geometry, MultiPolygon) else [flood_geometry]
    parts = [p for p in parts if not p.is_empty]
    out = np.zeros(len(lines))
    if not parts:
        return out
    geoms = lines.geometry.to_numpy()
    hit_lines, hit_parts = STRtree(parts).query(geoms, predicate="intersects")
    for i, j in zip(hit_lines, hit_parts, strict=True):
        out[i] += geoms[i].intersection(parts[j]).length
    return out


def classification(predicted: np.ndarray, actual: np.ndarray) -> dict[str, float]:
    tp = int((predicted & actual).sum())
    fp = int((predicted & ~actual).sum())
    fn = int((~predicted & actual).sum())
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else float("nan")
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
    }


def road_rule(roads, flood_m, thresholds: Iterable[float] = THRESHOLDS_M) -> pd.DataFrame:
    """Precision / recall of "closed if >= t m in the flood" against EMS road damage, per threshold."""
    analysed = roads[~roads.damage_gra.isin(UNKNOWN_GRADES)].reset_index(drop=True)
    inside = overlap_lengths(analysed, flood_m)
    rows = []
    for target, positives in (("severe", SEVERE), ("affected", AFFECTED)):
        actual = analysed.damage_gra.isin(positives).to_numpy()
        for t in thresholds:
            predicted = inside >= t if t > 0 else inside > 0
            rows.append(
                {"target": target, "threshold_m": t, "segments": len(analysed), **classification(predicted, actual)}
            )
    return pd.DataFrame(rows)


def damage_per_cell(points, resolution: int) -> pd.DataFrame:
    """Graded building points counted per hexagon (EPSG:4326 points)."""
    import h3

    pts = points.to_crs("EPSG:4326")
    cells = [h3.latlng_to_cell(y, x, resolution) for x, y in zip(pts.geometry.x, pts.geometry.y, strict=True)]
    df = pd.DataFrame({"h3": cells, "grade": points.damage_gra.to_numpy()})
    g = df.groupby("h3").grade
    return pd.DataFrame(
        {
            "severe": g.apply(lambda s: int(s.isin(SEVERE).sum())),
            "affected": g.apply(lambda s: int(s.isin(AFFECTED).sum())),
        }
    ).reset_index()


def capture(score: pd.Series, target: pd.Series, fractions: Iterable[float] = CAPTURE_FRACTIONS) -> dict[str, float]:
    """Share of the target that falls in the top fraction of hexagons by score. Ties are broken against the
    score (stable sort on a shuffled index would hide them), so a constant score earns no credit."""
    total = float(target.sum())
    out: dict[str, float] = {}
    order = np.lexsort((target.to_numpy(), -score.fillna(-np.inf).to_numpy()))  # within ties, lowest target first
    ranked_target = target.to_numpy()[order]
    for f in fractions:
        k = max(1, round(f * len(score)))
        out[f"top{int(f * 100)}"] = round(float(ranked_target[:k].sum()) / total, 3) if total else float("nan")
    return out


def rank_metrics(hexes: pd.DataFrame, target: str, scores: Iterable[str] = RANKING_SCORES) -> dict[str, Any]:
    t = hexes[target].fillna(0)
    out: dict[str, Any] = {"hexes": len(hexes), "target_total": int(t.sum())}
    for name in scores:
        if name not in hexes:
            continue
        s = hexes[name].fillna(0).astype(float)
        rho = s.rank().corr(t.rank()) if s.nunique() > 1 and t.nunique() > 1 else float("nan")
        out[name] = {"spearman": round(float(rho), 3), **capture(s, t)}
    out["random"] = {f"top{int(f * 100)}": f for f in CAPTURE_FRACTIONS}
    return out


def _town(root: Path) -> str:
    aoi = _layer(root, "areaOfInterestA")
    name = str(aoi.locality.iloc[0]) if aoi is not None and "locality" in aoi else root.name
    return name


def _key(root: Path) -> str:
    return root.name.split("_")[1].lower()  # e.g. aoi07


def _rank_town(vpaths: Paths, d: Path, key: str, buildings, source=None) -> dict[str, Any]:
    """satellite -> reach -> exposure -> fuse for one graded town, then the ranking metrics."""
    from floodline.stages import exposure, fuse, reach, satellite

    out: dict[str, Any] = {}
    sat_csv = vpaths.results / f"satellite_{key}.csv"
    layer = satellite.run(vpaths, d, out_name=sat_csv.name, resolution=9)
    reach.run(vpaths, layer.h3.tolist(), d, out_stem=f"reach_{key}", source=source)
    exposure.run(vpaths, sat_csv, d, vpaths.results / f"reach_{key}.csv", out_name=f"exposure_{key}.csv", source=source)
    hexes = fuse.run(
        vpaths,
        [sat_csv, vpaths.results / f"reach_{key}.csv", vpaths.results / f"exposure_{key}.csv"],
        out_stem=f"hexes_{key}",
    )
    dmg = damage_per_cell(buildings, resolution=9)
    merged = hexes.merge(dmg, on="h3", how="left").fillna({"severe": 0, "affected": 0})
    merged[["h3", "priority", "satellite_water_fraction", "buildings_in_flood", "hazard", "severe", "affected"]].to_csv(
        vpaths.results / f"ranking_{key}.csv", index=False
    )
    out["severe_in_mapped_hexes"] = int(merged.severe.sum())
    out["ranking_severe"] = rank_metrics(merged, "severe")
    out["ranking_affected"] = rank_metrics(merged, "affected")
    return out


def run(
    paths: Paths,
    gra_root: Path,
    ranking: bool = True,
    osm_pbfs: list[Path] | None = None,
    only: set[str] | None = None,
) -> dict[str, Any]:
    """``osm_pbfs``: Geofabrik extracts to read OSM from instead of Overpass. ``only``: rank just these AOI keys."""
    import geopandas as gpd

    from floodline import osm
    from floodline.stages import satellite

    vpaths = Paths(root=paths.root, namespace="validation").ensure()
    source = osm.OsmSource(osm_pbfs or (), paths.data / "external" / "osm" / "extracts")
    manifest = RunManifest("validate", params={"gra_root": str(gra_root), "thresholds_m": THRESHOLDS_M})
    dirs = sorted(d for d in gra_root.iterdir() if d.is_dir() and d.name.endswith("_vector"))
    road_tables, towns = [], {}
    for d in dirs:
        key, town = _key(d), _town(d)
        flood, _, _, _ = satellite.load_ems(d)
        roads = _layer(d, "transportationL")
        if roads is not None and len(roads):
            crs = roads.to_crs("EPSG:4326").estimate_utm_crs()
            flood_m = gpd.GeoSeries([flood], crs="EPSG:4326").to_crs(crs).iloc[0]
            table = road_rule(roads.to_crs(crs), flood_m).assign(aoi=key, town=town)
            road_tables.append(table)
        buildings = _layer(d, "builtUpP")
        severe = int(buildings.damage_gra.isin(SEVERE).sum()) if buildings is not None else 0
        towns[key] = {"town": town, "severe_buildings": severe}
        if not ranking or severe < MIN_SEVERE_BUILDINGS or (only and key not in only):
            continue
        try:
            towns[key].update(_rank_town(vpaths, d, key, buildings, source))
        except Exception as exc:  # one town's OSM failure must not discard the others
            log.error("ranking for %s (%s) failed: %s", key, town, exc)
            towns[key]["ranking_error"] = f"{type(exc).__name__}: {exc}"[:300]

    roads_all = pd.concat(road_tables, ignore_index=True) if road_tables else pd.DataFrame()
    roads_all.to_csv(paths.results / "validation_roads.csv", index=False)
    pooled = (
        roads_all.groupby(["target", "threshold_m"])[["tp", "fp", "fn"]].sum().reset_index()
        if len(roads_all)
        else pd.DataFrame()
    )
    if len(pooled):
        pooled["precision"] = (pooled.tp / (pooled.tp + pooled.fp)).round(3)
        pooled["recall"] = (pooled.tp / (pooled.tp + pooled.fn)).round(3)
        pooled["f1"] = (2 * pooled.precision * pooled.recall / (pooled.precision + pooled.recall)).round(3)
    summary = {"towns": towns, "roads_pooled": pooled.to_dict("records")}
    manifest.finish(**summary).write(paths.results / "validation_run.json")
    if ranking:
        summary["ranking"] = ranking_summary(paths)
    log.info("validation: %d towns, %d road rows", len(towns), len(roads_all))
    return summary


def ranking_summary(paths: Paths) -> dict[str, Any]:
    """Ranking metrics from every ``results/validation/ranking_*.csv``, per town and pooled, independent of
    which run produced them. Written to ``results/validation_ranking.json``."""
    vdir = Paths(root=paths.root, namespace="validation").results
    towns: dict[str, Any] = {}
    frames = []
    for f in sorted(vdir.glob("ranking_*.csv")):
        key = f.stem.removeprefix("ranking_")
        df = pd.read_csv(f)
        frames.append(df.assign(aoi=key))
        towns[key] = {"severe": rank_metrics(df, "severe"), "affected": rank_metrics(df, "affected")}
    out: dict[str, Any] = {"towns": towns}
    if frames:
        pooled = pd.concat(frames, ignore_index=True)
        # Pooled capture ranks hexagons across towns, so it answers "if a coordinator had all four towns".
        out["pooled"] = {"severe": rank_metrics(pooled, "severe"), "affected": rank_metrics(pooled, "affected")}
    manifest = RunManifest("validation_ranking", params={"towns": sorted(towns)})
    manifest.finish(**out).write(paths.results / "validation_ranking.json")
    return out
