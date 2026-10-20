"""Stage 6: a static web map of the ranked hexes and the evidence behind them.

``floodline serve`` adds one city to the map at ``docs/map/`` (served by GitHub Pages under /map/):

    docs/map/index.html             the map page (MapLibre, no build step, no server)
    docs/map/cities.json            one entry per city: title, view, headline numbers
    docs/map/data/<city>/*.geojson  hexes, flood extent, mapped area, closed roads, staging points, photos

Hexes carry only the fields the page shows, rounded, and only where there is something to show (priority > 0, a
photo, or cut off), so a city stays well under a megabyte. PMTiles is the step up for a whole province.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from floodline.log import get_logger
from floodline.stages.fuse import hex_geometry

log = get_logger(__name__)

HEX_FIELDS: tuple[str, ...] = (
    "h3",
    "priority",
    "confidence",
    "exposure",
    "hazard",
    "access",
    "buildings_affected",
    "buildings_in_flood",
    "satellite_water_fraction",
    "permanent_water_fraction",
    "reach_dry_min",
    "reach_flood_min",
    "delay_min",
    "cut_off",
    "n_reports",
    "needs_verification",
)
PHOTO_FIELDS: tuple[str, ...] = (
    "image_id",
    "title",
    "license",
    "source_url",
    "captured_at",
    "rel_flooding",
    "rel_depth",
    "d_flood_m",
    "d_permanent_m",
)
SIMPLIFY_DEG = 0.00002  # ~2 m; far below the 10-20 m placement accuracy of the source polygons
# Province-scale floods (Sindh 2022: 500 km2 in one study area) have millions of vertices; the tolerance grows
# until the outline fits a phone's budget. 0.0002 deg (~20 m) is still within the polygons' placement accuracy.
SIMPLIFY_STEPS_DEG: tuple[float, ...] = (0.00002, 0.00005, 0.0001, 0.0002)
MAX_OUTLINE_BYTES = 3_000_000


def outline(geometry, kind: str) -> tuple[dict[str, Any], float]:
    """The first simplification that fits ``MAX_OUTLINE_BYTES``, and the tolerance used."""
    feature: dict[str, Any] = {}
    for tol in SIMPLIFY_STEPS_DEG:
        feature = _feature(geometry.simplify(tol), {"kind": kind, "simplified_deg": tol})
        if len(json.dumps(feature, separators=(",", ":"))) <= MAX_OUTLINE_BYTES:
            return feature, tol
    return feature, SIMPLIFY_STEPS_DEG[-1]


def _clean(v: Any) -> Any:
    if isinstance(v, (np.ndarray, list, tuple)):  # OSM tags can be multi-valued, e.g. highway=[primary, trunk]
        return ", ".join(str(x) for x in v)
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        return round(v, 4)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def _round_coords(obj: Any, nd: int = 6) -> Any:
    if isinstance(obj, (list, tuple)):
        return [_round_coords(o, nd) for o in obj]
    return round(obj, nd) if isinstance(obj, float) else obj


def _feature(geometry, props: dict[str, Any]) -> dict[str, Any]:
    geo = dict(geometry.__geo_interface__)
    geo["coordinates"] = _round_coords(geo["coordinates"])
    return {"type": "Feature", "geometry": geo, "properties": {k: _clean(v) for k, v in props.items()}}


def _write(features: list[dict[str, Any]], out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), "utf-8")
    return out


def write_geojson(hexes: pd.DataFrame, out: Path, fields: tuple[str, ...] | None = None) -> Path:
    cols = [c for c in (fields or hexes.columns) if c in hexes.columns]
    polys = hex_geometry(hexes.h3)
    rows = hexes[cols].to_dict("records")
    return _write([_feature(p, r) for p, r in zip(polys, rows, strict=True)], out)


def visible_hexes(hexes: pd.DataFrame) -> pd.DataFrame:
    keep = hexes.priority.fillna(0) > 0
    for c in ("n_reports", "cut_off"):
        if c in hexes:
            keep |= hexes[c].fillna(0).astype(bool)
    return hexes[keep]


def _layer_features(gdf) -> list[dict[str, Any]]:
    props = [c for c in gdf.columns if c != "geometry"]
    return [
        _feature(geom, dict(zip(props, vals, strict=True)))
        for geom, *vals in gdf[["geometry", *props]].itertuples(index=False)
    ]


def build_city(
    key: str,
    title: str,
    subtitle: str,
    hexes_csv: Path,
    ems_source: Path,
    out_dir: Path,
    roads_geojson: Path | None = None,
    staging_geojson: Path | None = None,
    photos_csv: Path | None = None,
) -> dict[str, Any]:
    import geopandas as gpd
    import h3

    from floodline.stages.satellite import load_ems

    data = out_dir / "data" / key
    hexes = pd.read_csv(hexes_csv)
    shown = visible_hexes(hexes)
    write_geojson(shown, data / "hexes.geojson", HEX_FIELDS)

    flood, aoi, _, info = load_ems(ems_source)
    flood_feature, tolerance = outline(flood, "flood")
    _write([flood_feature], data / "flood.geojson")
    _write([_feature(aoi.boundary.simplify(SIMPLIFY_DEG), {"kind": "mapped_area"})], data / "aoi.geojson")

    n_roads = n_staging = n_photos = 0
    if roads_geojson is not None and roads_geojson.exists():
        roads = gpd.read_file(roads_geojson)
        n_roads = len(roads)
        _write(_layer_features(roads), data / "roads.geojson")
    if staging_geojson is not None and staging_geojson.exists():
        st = gpd.read_file(staging_geojson)
        n_staging = len(st)
        _write(_layer_features(st), data / "staging.geojson")
    if photos_csv is not None and photos_csv.exists():
        ph = pd.read_csv(photos_csv).dropna(subset=["lat", "lon"])
        pts = gpd.GeoDataFrame(ph, geometry=gpd.points_from_xy(ph.lon, ph.lat), crs="EPSG:4326")
        pts = pts[pts.within(aoi)].drop_duplicates("image_id")
        n_photos = len(pts)
        _write(_layer_features(pts[[c for c in PHOTO_FIELDS if c in pts] + ["geometry"]]), data / "photos.geojson")

    minx, miny, maxx, maxy = aoi.bounds
    top = shown.sort_values("priority", ascending=False).head(5)
    entry = {
        "key": key,
        "title": title,
        "subtitle": subtitle,
        "bounds": [round(minx, 5), round(miny, 5), round(maxx, 5), round(maxy, 5)],
        "activation": ems_source.name,
        "flood_outline_simplified_deg": tolerance,
        "stats": {
            "mapped_km2": info["aoi_km2"],
            "flood_km2": info["flood_km2"],
            "hexes": len(hexes),
            "hexes_with_priority": int((hexes.priority > 0).sum()),
            "hexes_cut_off": int(hexes.get("cut_off", pd.Series(False)).fillna(False).astype(bool).sum()),
            "buildings_in_flood": int(hexes.get("buildings_in_flood", pd.Series(0)).fillna(0).sum()),
            "closed_road_segments": n_roads,
            "staging_points": n_staging,
            "photos": n_photos,
            "resolution": h3.get_resolution(hexes.h3.iloc[0]) if len(hexes) else None,
        },
        "top": [
            {
                "h3": r.h3,
                "lat": round(h3.cell_to_latlng(r.h3)[0], 5),
                "lon": round(h3.cell_to_latlng(r.h3)[1], 5),
                "priority": round(float(r.priority), 3),
                "buildings_affected": int(getattr(r, "buildings_affected", 0) or 0),
                "cut_off": bool(getattr(r, "cut_off", False)),
            }
            for r in top.itertuples(index=False)
        ],
    }
    log.info("map %s: %d of %d hexes shown, %d photos, %d closed roads", key, len(shown), len(hexes), n_photos, n_roads)
    return entry


def write_site(out_dir: Path, entry: dict[str, Any]) -> Path:
    """Add or replace one city in ``cities.json`` and (re)write the page."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "cities.json"
    cities = json.loads(manifest.read_text("utf-8")) if manifest.exists() else []
    cities = [c for c in cities if c["key"] != entry["key"]] + [entry]
    manifest.write_text(json.dumps(cities, indent=1, ensure_ascii=False), "utf-8")
    page = out_dir / "index.html"
    page.write_text(resources.files("floodline.web").joinpath("map.html").read_text("utf-8"), "utf-8")
    (out_dir / ".nojekyll").touch()
    return page


def run(
    paths,
    hexes_csv: Path,
    key: str,
    title: str,
    ems_source: Path,
    subtitle: str = "",
    out_dir: Path | None = None,
    roads: Path | None = None,
    staging: Path | None = None,
    photos: Path | None = None,
) -> Path:
    out_dir = out_dir or paths.root / "docs" / "map"
    entry = build_city(key, title, subtitle, hexes_csv, ems_source, out_dir, roads, staging, photos)
    page = write_site(out_dir, entry)
    log.info("site: %s", page)
    return page
