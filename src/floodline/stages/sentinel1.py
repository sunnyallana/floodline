"""Stage 4d: an independent water mask from Sentinel-1 radar, checked against the EMS flood polygons.

EMS rapid-mapping polygons are interpreted by analysts, often from optical imagery. Sentinel-1 C-band radar sees
through cloud, and calm open water returns almost nothing to the sensor, so low VV backscatter marks water.
This stage builds a water mask from one radiometrically terrain-corrected (RTC) scene, served free by the
Microsoft Planetary Computer, and measures how well it agrees with EMS:

1. pick the RTC scene covering the area closest in time to a reference date (the EMS image date);
2. read VV over the mapped area at ``RESOLUTION_M`` (a COG overview, a few MB);
3. threshold in decibels with Otsu's method on the area's own histogram, the standard unsupervised choice for
   bimodal water/land backscatter, and remove EMS permanent water so both masks describe flooding only;
4. per pixel: IoU, precision and recall of the radar mask against the rasterised EMS flood polygons;
5. per H3 hexagon: radar water fraction next to the EMS fraction, and their rank correlation.

Radar misses water among buildings (double bounce) and under vegetation, and smooth tarmac or sand can look
like water, so disagreement is expected in towns. That is why it is a second source, not a replacement.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-rtc"
COLLECTION = "sentinel-1-rtc"
RESOLUTION_M = 20.0
WINDOW_DAYS = 6
# Change detection (the standard operational approach, e.g. UN-SPIDER's recommended practice): a pixel is new
# water when it is dark after the event and clearly darker than in a pre-event scene of the same orbit. A single
# post-event threshold fails when the area is mostly dry and the histogram is not bimodal.
PRE_WINDOW_DAYS = (10, 60)  # pre-event scene between 10 and 60 days before the reference date
DROP_DB = 3.0  # backscatter must fall by at least this much
ABS_DB_RANGE = (-22.0, -15.0)  # the post-event Otsu threshold is clamped to plausible open-water values
METHODS: tuple[str, ...] = ("change", "otsu")


def otsu(values: np.ndarray, bins: int = 256) -> float:
    """Otsu threshold of a 1-D sample: the cut that maximises between-class variance."""
    v = values[np.isfinite(values)]
    hist, edges = np.histogram(v, bins=bins)
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * centres) / np.maximum(w0, 1)
    m1 = (np.sum(hist * centres) - np.cumsum(hist * centres)) / np.maximum(w1, 1)
    between = w0 * w1 * (m0 - m1) ** 2
    return float(centres[int(np.argmax(between[:-1]))])


def agreement(radar: np.ndarray, reference: np.ndarray, valid: np.ndarray) -> dict[str, float]:
    """Pixel agreement of two boolean water masks over ``valid`` pixels."""
    r, t = radar & valid, reference & valid
    tp = int((r & t).sum())
    fp = int((r & ~t).sum())
    fn = int((~r & t).sum())
    union = tp + fp + fn
    return {
        "iou": round(tp / union, 3) if union else float("nan"),
        "precision": round(tp / (tp + fp), 3) if tp + fp else float("nan"),
        "recall": round(tp / (tp + fn), 3) if tp + fn else float("nan"),
        "radar_water_share": round(float(r.sum() / max(valid.sum(), 1)), 3),
        "ems_water_share": round(float(t.sum() / max(valid.sum(), 1)), 3),
    }


def search(
    bounds: tuple[float, float, float, float],
    when: datetime,
    days: int = WINDOW_DAYS,
    window: tuple[datetime, datetime] | None = None,
    orbit: int | None = None,
) -> dict:
    """The RTC item covering ``bounds`` closest to ``when`` within +/- ``days`` (or inside ``window``), optionally
    restricted to one relative orbit so a pre-event scene shares the post-event geometry."""
    import requests
    from shapely.geometry import box, shape

    lo, hi = window or (when - pd.Timedelta(days=days), when + pd.Timedelta(days=days))
    start, end = lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")
    body: dict[str, Any] = {
        "collections": [COLLECTION],
        "bbox": list(bounds),
        "datetime": f"{start}/{end}",
        "limit": 100,
    }
    feats = requests.post(f"{STAC}/search", json=body, timeout=120).json().get("features", [])
    area = box(*bounds)
    covering = [f for f in feats if shape(f["geometry"]).contains(area)]
    if orbit is not None:
        covering = [f for f in covering if f["properties"].get("sat:relative_orbit") == orbit]
    if not covering:
        raise RuntimeError(f"no Sentinel-1 RTC scene covers {bounds} within {days} days of {when:%Y-%m-%d}")

    def gap(f: dict) -> float:
        return abs((pd.Timestamp(f["properties"]["datetime"]).tz_localize(None) - pd.Timestamp(when)).total_seconds())

    return min(covering, key=gap)


def read_vv(item: dict, bounds_4326: tuple[float, float, float, float], resolution_m: float = RESOLUTION_M):
    """VV backscatter in dB over the box, resampled to ``resolution_m``; returns (array, transform, crs)."""
    import rasterio
    import requests
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    token = requests.get(TOKEN, timeout=60).json()["token"]
    with rasterio.open(item["assets"]["vv"]["href"] + "?" + token) as src:
        left, bottom, right, top = transform_bounds("EPSG:4326", src.crs, *bounds_4326, densify_pts=21)
        window = from_bounds(left, bottom, right, top, src.transform).round_offsets().round_lengths()
        scale = resolution_m / src.res[0]
        out_shape = (max(1, int(window.height / scale)), max(1, int(window.width / scale)))
        data = src.read(1, window=window, out_shape=out_shape, resampling=Resampling.average, masked=True)
        transform = src.window_transform(window) * src.window_transform(window).scale(
            window.width / out_shape[1], window.height / out_shape[0]
        )
        crs = src.crs
    linear = np.ma.filled(data.astype("float64"), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = 10.0 * np.log10(np.where(linear > 0, linear, np.nan))
    return db, transform, crs


def rasterise(geometry, shape: tuple[int, int], transform, crs) -> np.ndarray:
    import geopandas as gpd
    from rasterio.features import rasterize

    if geometry is None or geometry.is_empty:
        return np.zeros(shape, dtype=bool)
    g = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(crs).iloc[0]
    return rasterize([(g, 1)], out_shape=shape, transform=transform, fill=0, dtype="uint8").astype(bool)


def per_hex(mask: np.ndarray, valid: np.ndarray, transform, crs, resolution: int = 9) -> pd.DataFrame:
    """Share of valid pixels flagged in each H3 cell (pixel centres assigned to cells)."""
    import h3
    from pyproj import Transformer

    rows, cols = np.nonzero(valid)
    xs, ys = transform * (cols + 0.5, rows + 0.5)
    lon, lat = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform(xs, ys)
    cells = [h3.latlng_to_cell(b, a, resolution) for a, b in zip(lon, lat, strict=True)]
    df = pd.DataFrame({"h3": cells, "water": mask[rows, cols]})
    return df.groupby("h3").water.mean().rename("radar_water_fraction").reset_index()


def run(paths: Paths, ems_source: Path, when: str, out_stem: str, resolution: int = 9) -> dict[str, Any]:
    from floodline.stages.satellite import load_ems

    flood, aoi, permanent, _ = load_ems(ems_source)
    reference_date = datetime.fromisoformat(when)
    item = search(aoi.bounds, reference_date)
    db, transform, crs = read_vv(item, aoi.bounds)
    inside = rasterise(aoi, db.shape, transform, crs)
    valid = inside & np.isfinite(db)
    permanent_px = rasterise(permanent, db.shape, transform, crs) if permanent is not None else np.zeros_like(valid)
    threshold = otsu(db[valid])
    radar_water = (db < threshold) & valid & ~permanent_px
    ems_flood = rasterise(flood, db.shape, transform, crs) & valid & ~permanent_px
    scored = valid & ~permanent_px

    pixel = agreement(radar_water, ems_flood, scored)
    hexes = per_hex(radar_water, scored, transform, crs, resolution).merge(
        per_hex(ems_flood, scored, transform, crs, resolution).rename(columns={"radar_water_fraction": "ems_fraction"}),
        on="h3",
    )
    hexes.to_csv(paths.results / f"{out_stem}.csv", index=False)
    rho = hexes.radar_water_fraction.rank().corr(hexes.ems_fraction.rank()) if len(hexes) > 2 else float("nan")
    summary = {
        "scene": item["id"],
        "acquired": item["properties"]["datetime"],
        "reference_date": when,
        "hours_from_reference": round(
            abs(
                (
                    pd.Timestamp(item["properties"]["datetime"]).tz_localize(None) - pd.Timestamp(reference_date)
                ).total_seconds()
            )
            / 3600,
            1,
        ),
        "resolution_m": RESOLUTION_M,
        "otsu_threshold_db": round(threshold, 2),
        "pixels_scored": int(scored.sum()),
        **pixel,
        "hexes": len(hexes),
        "hex_spearman": round(float(rho), 3),
    }
    manifest = RunManifest("sentinel1", params={"ems_source": str(ems_source), "when": when, "collection": COLLECTION})
    manifest.finish(**summary).write(paths.results / f"{out_stem}_run.json")
    log.info("sentinel-1 %s: %s", out_stem, summary)
    return summary


# ---------------------------------------------------------------- a radar-only flood map, for the ablation

MIN_POLYGON_PIXELS = 4  # radar speckle: water patches smaller than this (0.16 ha at 20 m) are dropped


def post_event_date(package: Path) -> datetime | None:
    """Date of the latest post-event image EMS used for this product, from its source table or XML metadata."""
    import re

    import geopandas as gpd

    for f in sorted(package.glob("*source*.dbf")):
        table = gpd.read_file(f)
        if {"eventphase", "src_date"} <= set(table.columns):
            dates = [
                datetime.strptime(d, "%d/%m/%Y")
                for d in table.loc[table.eventphase.astype(str).str.lower() == "post-event", "src_date"].astype(str)
                if re.fullmatch(r"\d{2}/\d{2}/\d{4}", d)
            ]
            if dates:
                return max(dates)
    for f in sorted(package.glob("*observedEventA*.xml")):
        found = re.findall(r"<gco:Date>(\d{4}-\d{2}-\d{2})", f.read_text(encoding="utf-8", errors="ignore"))
        if found:
            return datetime.fromisoformat(found[0])
    return None


def pre_event_on_grid(
    item: dict, bounds, shape: tuple[int, int], transform, crs, when: datetime
) -> tuple[np.ndarray, dict]:
    """The latest pre-event scene of the same relative orbit, resampled onto the post-event grid (dB)."""
    from rasterio.warp import Resampling, reproject

    pre = search(
        bounds,
        when,
        window=(when - pd.Timedelta(days=PRE_WINDOW_DAYS[1]), when - pd.Timedelta(days=PRE_WINDOW_DAYS[0])),
        orbit=item["properties"].get("sat:relative_orbit"),
    )
    db_pre, t_pre, crs_pre = read_vv(pre, bounds)
    out = np.full(shape, np.nan)
    reproject(db_pre, out, src_transform=t_pre, src_crs=crs_pre, dst_transform=transform, dst_crs=crs,
              resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)  # fmt: skip
    return out, pre


def water_mask(db: np.ndarray, valid: np.ndarray, pre_db: np.ndarray | None, method: str) -> tuple[np.ndarray, float]:
    """Boolean new-water mask and the absolute threshold used. ``method``: ``change`` or ``otsu``."""
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    t = otsu(db[valid])
    if method == "otsu":
        return (db < t) & valid, t
    t = float(np.clip(t, *ABS_DB_RANGE))
    if pre_db is None:
        raise ValueError("change detection needs a pre-event scene")
    with np.errstate(invalid="ignore"):
        drop = (pre_db - db) >= DROP_DB
    return (db < t) & drop & valid & np.isfinite(pre_db), t


def radar_flood(package: Path, when: datetime, method: str = "change") -> tuple[Any, dict[str, Any]]:
    """Flood polygons (EPSG:4326) from Sentinel-1 alone over the package's mapped area, permanent water removed."""
    import geopandas as gpd
    from rasterio.features import shapes
    from shapely.geometry import shape as to_shape
    from shapely.ops import unary_union

    from floodline.stages.satellite import load_ems

    _, aoi, permanent, _ = load_ems(package)
    item = search(aoi.bounds, when)
    db, transform, crs = read_vv(item, aoi.bounds)
    valid = rasterise(aoi, db.shape, transform, crs) & np.isfinite(db)
    permanent_px = rasterise(permanent, db.shape, transform, crs) if permanent is not None else np.zeros_like(valid)
    pre_db, pre_item = (None, None)
    if method == "change":
        pre_db, pre_item = pre_event_on_grid(item, aoi.bounds, db.shape, transform, crs, when)
    mask, threshold = water_mask(db, valid, pre_db, method)
    water = mask & ~permanent_px
    pixel_area = abs(transform.a * transform.e)
    polys = [
        to_shape(g)
        for g, v in shapes(water.astype("uint8"), mask=water, transform=transform)
        if v == 1 and to_shape(g).area >= MIN_POLYGON_PIXELS * pixel_area
    ]
    geom = unary_union(polys) if polys else None
    flood = gpd.GeoSeries([geom], crs=crs).to_crs("EPSG:4326").iloc[0] if geom is not None else None
    info = {
        "method": method,
        "scene": item["id"],
        "acquired": item["properties"]["datetime"],
        "pre_scene": pre_item["id"] if pre_item else None,
        "threshold_db": round(threshold, 2),
        "water_share_of_area": round(float(water.sum() / max(valid.sum(), 1)), 3),
    }
    return flood, info


def write_radar_package(
    package: Path, out_dir: Path, when: datetime, method: str = "change"
) -> tuple[Path, dict[str, Any]]:
    """A copy of an EMS package whose flood layer is replaced by the radar flood map.

    Area of interest and permanent water are kept from EMS (they describe where analysts looked and the normal
    river, not the flood), so every downstream stage runs unchanged on the radar version.
    """
    import geopandas as gpd

    from floodline.stages.satellite import AOI_LAYER_HINTS, PERMANENT_WATER_HINTS, _find_layers

    out = out_dir / f"{package.name}_S1{method}"
    out.mkdir(parents=True, exist_ok=True)
    flood, info = radar_flood(package, when, method)
    geoms = [] if flood is None or flood.is_empty else [flood]
    gpd.GeoDataFrame({"event_type": ["5-Flood"] * len(geoms)}, geometry=geoms, crs="EPSG:4326").to_file(
        out / "S1_observedEventA.gpkg", driver="GPKG"
    )
    for hints, name in ((AOI_LAYER_HINTS, "S1_areaOfInterestA.gpkg"), (PERMANENT_WATER_HINTS, "S1_hydrographyA.gpkg")):
        files = _find_layers(package, hints)
        if files:
            gpd.read_file(files[0]).to_file(out / name, driver="GPKG")
    return out, info
