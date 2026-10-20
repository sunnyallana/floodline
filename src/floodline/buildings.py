"""Building footprints where OpenStreetMap is thin: Microsoft Global ML Building Footprints.

OSM building coverage is near-complete in central Europe and close to empty in much of rural South Asia: around
Larkana (EMSR629, Sindh 2022) OSM has 108 buildings in the 7,663 hexagons that matter. Microsoft's footprints,
detected from imagery and released under ODbL, cover the same area densely. They are tiled by Bing Maps quadkey
at zoom 9; ``dataset-links.csv`` maps each tile to a gzipped GeoJSON-lines file.

Only the tiles that cover the requested box are downloaded, and each is cached under
``data/external/buildings/ms/``. The release date is in every URL, so a cached tile is a pinned input.
"""

from __future__ import annotations

import gzip
import json
import math
from pathlib import Path

import pandas as pd

from floodline.log import get_logger

log = get_logger(__name__)

INDEX_URL = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
ZOOM = 9


def tile_xy(lon: float, lat: float, zoom: int = ZOOM) -> tuple[int, int]:
    """Web Mercator tile containing a point."""
    lat = max(min(lat, 85.05112878), -85.05112878)
    n = 2**zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def quadkey(x: int, y: int, zoom: int = ZOOM) -> str:
    digits = []
    for i in range(zoom, 0, -1):
        mask = 1 << (i - 1)
        digits.append(str((1 if x & mask else 0) + (2 if y & mask else 0)))
    return "".join(digits)


def quadkeys(bounds: tuple[float, float, float, float], zoom: int = ZOOM) -> list[str]:
    """Every zoom-``zoom`` quadkey whose tile intersects ``bounds`` (minx, miny, maxx, maxy in lon/lat)."""
    minx, miny, maxx, maxy = bounds
    x0, y1 = tile_xy(minx, miny, zoom)
    x1, y0 = tile_xy(maxx, maxy, zoom)
    return sorted(quadkey(x, y, zoom) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def _index(cache_dir: Path) -> pd.DataFrame:
    path = cache_dir / "ms-dataset-links.csv"
    if not path.exists():
        import requests

        cache_dir.mkdir(parents=True, exist_ok=True)
        r = requests.get(INDEX_URL, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
    return pd.read_csv(path, dtype={"QuadKey": str})


def _tile(url: str, cache_dir: Path) -> Path:
    import requests

    name = url.split("/global-buildings/", 1)[1].replace("/", "_").replace("=", "-")
    out = cache_dir / "ms" / name
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(url, timeout=600)
        r.raise_for_status()
        tmp = out.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(out)
    return out


def read_geojsonl(path: Path, bounds: tuple[float, float, float, float]):
    """Footprints from one gzipped GeoJSON-lines tile whose centroid falls inside ``bounds``."""
    import geopandas as gpd
    from shapely.geometry import shape

    minx, miny, maxx, maxy = bounds
    geoms = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            g = shape(json.loads(line)["geometry"])
            c = g.centroid
            if minx <= c.x <= maxx and miny <= c.y <= maxy:
                geoms.append(g)
    return gpd.GeoDataFrame(geometry=geoms, crs="EPSG:4326")


def microsoft(bounds: tuple[float, float, float, float], cache_dir: Path):
    """Microsoft ML footprints inside ``bounds`` as a GeoDataFrame (EPSG:4326)."""
    import geopandas as gpd

    index = _index(cache_dir)
    wanted = set(quadkeys(bounds))
    rows = index[index.QuadKey.isin(wanted)]
    frames = [read_geojsonl(_tile(u, cache_dir), bounds) for u in rows.Url]
    log.info("microsoft buildings: %d tiles for %d quadkeys", len(rows), len(wanted))
    if not frames:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
