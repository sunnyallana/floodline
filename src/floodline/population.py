"""Residential population per hexagon from GHSL, the standard humanitarian "people in the flood" layer.

GHS-POP R2023A (European Commission, Joint Research Centre), epoch 2020, 100 m, Mollweide (ESRI:54009), in
1,000 km tiles named ``R{row}_C{col}``. Analysts estimate people affected by overlaying it on a flood extent;
``people_in_flood`` per hexagon is that estimate, and the validation uses it as the practitioner baseline that
Floodline's priority has to beat.

Only the tiles that cover the requested box are downloaded and cached under ``data/external/ghsl/``. A tile
that does not contain its box raises, so a wrong tile index cannot silently return zero people.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from floodline.log import get_logger

log = get_logger(__name__)

RELEASE = "GHS_POP_E2020_GLOBE_R2023A_54009_100"
BASE = f"https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_POP_GLOBE_R2023A/{RELEASE}/V1-0/tiles"
MOLLWEIDE = "ESRI:54009"
TILE_M = 1_000_000.0
X0, Y0 = -18_041_000.0, 9_000_000.0  # top-left corner of the tile grid


def tile_of(x: float, y: float) -> str:
    return f"R{int((Y0 - y) // TILE_M) + 1}_C{int((x - X0) // TILE_M) + 1}"


def tiles_for(bounds: tuple[float, float, float, float]) -> list[str]:
    """Tile ids covering a lon/lat box (via its corners and edge midpoints in Mollweide)."""
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:4326", MOLLWEIDE, always_xy=True)
    minx, miny, maxx, maxy = bounds
    xs = np.linspace(minx, maxx, 5)
    ys = np.linspace(miny, maxy, 5)
    return sorted({tile_of(*t.transform(x, y)) for x in xs for y in ys})


def _tile_path(tile: str, cache_dir: Path) -> Path:
    tif = cache_dir / f"{RELEASE}_V1_0_{tile}.tif"
    if tif.exists():
        return tif
    import requests

    cache_dir.mkdir(parents=True, exist_ok=True)
    zpath = cache_dir / f"{RELEASE}_V1_0_{tile}.zip"
    if not zpath.exists():
        with requests.get(f"{BASE}/{RELEASE}_V1_0_{tile}.zip", stream=True, timeout=600) as r:
            r.raise_for_status()
            part = zpath.with_suffix(".part")
            with open(part, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
            part.replace(zpath)
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(".tif"))
        with z.open(name) as src, open(tif, "wb") as dst:
            dst.write(src.read())
    return tif


def population_per_hex(flood_geometry, area, cells: list[str], cache_dir: Path) -> pd.DataFrame:
    """``people_total`` and ``people_in_flood`` per hexagon: GHSL pixels assigned by centre, the flood share by
    whether the pixel centre lies in the flood polygons."""
    import geopandas as gpd
    import h3
    import rasterio
    from pyproj import Transformer
    from rasterio.features import rasterize
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    if not cells:
        return pd.DataFrame(columns=["h3", "people_total", "people_in_flood"])
    resolution = h3.get_resolution(cells[0])
    wanted = set(cells)
    to_wgs = Transformer.from_crs(MOLLWEIDE, "EPSG:4326", always_xy=True)
    flood_m = gpd.GeoSeries([flood_geometry], crs="EPSG:4326").to_crs(MOLLWEIDE).iloc[0]
    frames = []
    for tile in tiles_for(area.bounds):
        with rasterio.open(_tile_path(tile, cache_dir)) as src:
            # Densified: box edges are curves in Mollweide, so the corners alone can clip the area.
            left, bottom, right, top = transform_bounds("EPSG:4326", MOLLWEIDE, *area.bounds, densify_pts=21)
            west, south = max(left, src.bounds.left), max(bottom, src.bounds.bottom)
            east, north = min(right, src.bounds.right), min(top, src.bounds.top)
            if west >= east or south >= north:
                continue
            window = from_bounds(west, south, east, north, src.transform).round_offsets().round_lengths()
            data = src.read(1, window=window, masked=True).filled(0).astype("float64")
            transform = src.window_transform(window)
        data[data < 0] = 0
        in_flood = rasterize([(flood_m, 1)], out_shape=data.shape, transform=transform, fill=0, dtype="uint8").astype(
            bool
        )
        rows, cols = np.nonzero(data > 0)
        if not len(rows):
            continue
        xs, ys = transform * (cols + 0.5, rows + 0.5)
        lon, lat = to_wgs.transform(xs, ys)
        hexes = [h3.latlng_to_cell(b_, a_, resolution) for a_, b_ in zip(lon, lat, strict=True)]
        frames.append(pd.DataFrame({"h3": hexes, "people": data[rows, cols], "flood": in_flood[rows, cols]}))
    if not frames:
        raise RuntimeError(f"no GHSL population found for {area.bounds}; check the tile index")
    df = pd.concat(frames, ignore_index=True)
    df = df[df.h3.isin(wanted)]
    order = sorted(wanted)
    out = pd.DataFrame(
        {
            "people_total": df.groupby("h3").people.sum().reindex(order),
            "people_in_flood": df[df.flood].groupby("h3").people.sum().reindex(order),
        }
    )
    out = out.fillna(0.0).round(1).rename_axis("h3").reset_index()
    log.info(
        "ghsl: %.0f people in %d hexagons, %.0f in the flood",
        out.people_total.sum(),
        len(out),
        out.people_in_flood.sum(),
    )
    return out
