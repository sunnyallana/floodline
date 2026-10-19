"""Photo ⇄ satellite agreement: do located photos fall where the satellite maps water?

For each located photo inside the mapped area (any hex present in a satellite layer), report whether its own hex
has satellite water, and whether any hex within ``k`` rings does. The ring check is the fair one: photographers
stand on the dry edge of a flood, and phone GPS is off by tens of metres, so a photo of water is often taken from
the neighbouring hex. Photos outside every mapped area are counted separately and never treated as disagreement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest
from floodline.stages.satellite import AOI_LAYER_HINTS, FLOOD_LAYER_HINTS, PERMANENT_WATER_HINTS, _find_layers

log = get_logger(__name__)


def load_layers(layer_csvs: list[Path], column: str = "satellite_water_fraction") -> pd.Series:
    """One fraction per hex across several satellite layers (max where AOIs overlap)."""
    frames = [pd.read_csv(p) for p in layer_csvs]
    frames = [f[["h3", column]] for f in frames if column in f]
    if not frames:
        return pd.Series(dtype=float, name=column)
    return pd.concat(frames).groupby("h3")[column].max()


def neighbourhood_max(cells: pd.Series, water: pd.Series, k: int = 1) -> pd.Series:
    import h3

    lookup = water.to_dict()
    return cells.map(lambda c: max((lookup.get(n, 0.0) for n in h3.grid_disk(c, k)), default=0.0))


def agreement(
    photos: pd.DataFrame, water: pd.Series, k: int = 1, permanent: pd.Series | None = None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    df = photos.dropna(subset=["h3"]).copy()
    df["in_mapped_area"] = df.h3.isin(water.index)
    mapped = df[df.in_mapped_area].copy()
    mapped["water_own_hex"] = mapped.h3.map(water).fillna(0.0)
    mapped[f"water_within_{k}"] = neighbourhood_max(mapped.h3, water, k)
    if permanent is not None and len(permanent):
        mapped[f"flood_or_permanent_within_{k}"] = (
            mapped[f"water_within_{k}"].to_numpy() + neighbourhood_max(mapped.h3, permanent, k).to_numpy()
        )

    def rates(sub: pd.DataFrame) -> dict[str, Any]:
        if sub.empty:
            return {"n": 0}
        out = {
            "n": len(sub),
            "own_hex_wet": round(float((sub.water_own_hex > 0).mean()), 3),
            f"within_{k}_ring_wet": round(float((sub[f"water_within_{k}"] > 0).mean()), 3),
        }
        col = f"flood_or_permanent_within_{k}"
        if col in sub:
            out[f"within_{k}_ring_flood_or_permanent"] = round(float((sub[col] > 0).mean()), 3)
        return out

    summary: dict[str, Any] = {
        "photos_located": len(df),
        "photos_in_mapped_area": len(mapped),
        "photos_outside_mapped_area": int((~df.in_mapped_area).sum()),
        "all_mapped": rates(mapped),
    }
    for flag in ("rel_flooding", "rel_depth"):
        if flag in mapped:
            summary[flag] = rates(mapped[mapped[flag].astype(bool)])
            summary[f"not_{flag}"] = rates(mapped[~mapped[flag].astype(bool)])
    return mapped, summary


def _quantiles(s: pd.Series) -> dict[str, float]:
    return {q: round(float(s.quantile(v)), 1) for q, v in (("p25", 0.25), ("median", 0.5), ("p75", 0.75))}


def _distance_summary(pts: pd.DataFrame, thresholds_m: tuple[int, ...]) -> dict[str, Any]:
    s: dict[str, Any] = {"n": len(pts), "d_flood_m": _quantiles(pts.d_flood_m)}
    has_perm = "d_permanent_m" in pts
    if has_perm:
        s["d_permanent_m"] = _quantiles(pts.d_permanent_m)
    for t in thresholds_m:
        near_flood = pts.d_flood_m <= t
        s[f"within_{t}m_flood"] = round(float(near_flood.mean()), 3)
        if has_perm:
            s[f"within_{t}m_flood_or_permanent"] = round(float((near_flood | (pts.d_permanent_m <= t)).mean()), 3)
    return s


def distances(
    photos: pd.DataFrame, ems_dirs: list[Path], thresholds_m: tuple[int, ...] = (50, 150, 300)
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Metre distance from each photo to the nearest mapped flood polygon and to permanent water, per activation.

    Only photos inside the activation's mapped area (AOI layer) are measured. Hex membership saturates near city
    rivers (every hex touches the channel), so distances are the evidence that separates "photo of a high river"
    from "photo of a flooded street". Batches of photos often share one copied geotag, so every summary is given
    twice: per photo and per unique location (rounded to 1e-5 degrees, about a metre).
    """
    import geopandas as gpd

    out_frames, summary = [], {}
    pts_all = gpd.GeoDataFrame(photos, geometry=gpd.points_from_xy(photos.lon, photos.lat), crs="EPSG:4326")
    for d in ems_dirs:
        layers = {}
        for name, hints in (
            ("flood", FLOOD_LAYER_HINTS),
            ("permanent", PERMANENT_WATER_HINTS),
            ("aoi", AOI_LAYER_HINTS),
        ):
            files = _find_layers(d, hints)
            if files:
                layers[name] = pd.concat([gpd.read_file(f) for f in files], ignore_index=True)
        if "flood" not in layers or "aoi" not in layers:
            continue
        crs = layers["flood"].to_crs("EPSG:4326").estimate_utm_crs()
        geoms = {name: gdf.to_crs(crs).geometry.buffer(0).union_all() for name, gdf in layers.items()}
        pts = pts_all.to_crs(crs)
        pts = pts[pts.within(geoms["aoi"])].copy()
        if pts.empty:
            continue
        pts["d_flood_m"] = pts.distance(geoms["flood"])
        if "permanent" in geoms:
            pts["d_permanent_m"] = pts.distance(geoms["permanent"])
        pts["ems_activation"] = d.name
        pts["location_key"] = pts.lat.round(5).astype(str) + "," + pts.lon.round(5).astype(str)
        frame = pd.DataFrame(pts.drop(columns="geometry"))
        out_frames.append(frame)
        unique = frame.drop_duplicates("location_key")
        summary[d.name] = {
            "per_photo": _distance_summary(frame, thresholds_m),
            "per_unique_location": _distance_summary(unique, thresholds_m),
        }
    return (pd.concat(out_frames, ignore_index=True) if out_frames else pd.DataFrame()), summary


def run(
    paths: Paths,
    photos_csv: Path,
    layer_csvs: list[Path],
    out_stem: str = "agreement",
    k: int = 1,
    ems_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    manifest = RunManifest(
        "agreement", params={"photos": str(photos_csv), "layers": [str(p) for p in layer_csvs], "k": k}
    )
    photos = pd.read_csv(photos_csv)
    water = load_layers(layer_csvs)
    permanent = load_layers(layer_csvs, "permanent_water_fraction")
    mapped, summary = agreement(photos, water, k, permanent=permanent)
    mapped.to_csv(paths.results / f"{out_stem}_photos.csv", index=False)
    if ems_dirs:
        dist, dist_summary = distances(photos.dropna(subset=["lat", "lon"]), ems_dirs)
        dist.to_csv(paths.results / f"{out_stem}_distances.csv", index=False)
        summary["distances"] = dist_summary
    manifest.finish(**summary).write(paths.results / f"{out_stem}_run.json")
    log.info("agreement: %s", summary)
    return summary
