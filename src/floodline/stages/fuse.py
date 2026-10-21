"""Stage 5: fuse the per-hex layers and located photo reports, and rank hexes for a responder.

Every hex in the mapped area is scored, not only hexes with photos: satellite extent is the coverage backbone and
photos are evidence where they exist. Layers are CSVs keyed by ``h3`` and joined onto the first one:

- satellite: ``satellite_water_fraction``, ``permanent_water_fraction``;
- reach: ``reach_dry_min``, ``reach_flood_min``, ``delay_min``, ``cut_off``;
- exposure: ``buildings_in_flood``, ``buildings_total``.

Reports (optional) need ``lat`` and ``lon``; assessment columns (``depth_class``, ``people_*``, ``confidence``) are
used when present. Field reports carry ``trust`` and ``device_id`` from ``stages/trust.py``; a report's claims move
priority only when corroborated (see ``score``), otherwise they go to the verification list.

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
    """Every tunable of the priority score in one place, so a sensitivity run can vary all of them."""

    exposure: float = 0.45
    hazard: float = 0.30
    access: float = 0.25
    exposure_scale: float = EXPOSURE_SCALE
    person_weight: float = PERSON_WEIGHT
    delay_saturation_min: float = DELAY_SATURATION_MIN
    # Ablation only: the first corroboration rule, under which device agreement overruled a mapped-dry
    # satellite and satellite water also confirmed people. attack.py measures what the stricter rule buys.
    permissive_corroboration: bool = False


def to_cells(df: pd.DataFrame, resolution: int = H3_RESOLUTION) -> pd.Series:
    import h3

    return pd.Series([h3.latlng_to_cell(a, b, resolution) for a, b in zip(df.lat, df.lon, strict=True)], index=df.index)


def _col(df: pd.DataFrame, name: str, default=np.nan) -> pd.Series:
    return df[name] if name in df else pd.Series(default, index=df.index)


DEPTH_ORDER: tuple[str, ...] = ("dry", "ankle", "knee", "waist", "chest", "above_head")
CORROBORATING_DEVICES = 2  # independent devices whose depth claims agree within one class


def _depth_ordinal(classes: pd.Series) -> pd.Series:
    return classes.map({c: i for i, c in enumerate(DEPTH_ORDER)})


def _class_at(ordinal: float) -> str | None:
    return DEPTH_ORDER[int(ordinal)] if pd.notna(ordinal) else None


def aggregate(reports: pd.DataFrame, resolution: int = H3_RESOLUTION) -> pd.DataFrame:
    """Per-hex evidence summary from located reports.

    Trust (see ``stages/trust.py``): ``rejected`` reports are dropped, ``review`` reports are only counted, and
    ``accepted`` reports become evidence. Reports without a ``trust`` column (archive photos, model assessments)
    count as accepted. Each device counts once, with its deepest claim in the hexagon. People are the maximum
    over reports, not the sum, so two phones photographing the same group do not double it.
    """
    r = reports.dropna(subset=["lat", "lon"]).copy()
    if r.empty:
        return pd.DataFrame(columns=["h3", "n_reports"])
    r["h3"] = to_cells(r, resolution)
    trust = _col(r, "trust", "accepted").fillna("accepted")
    unverified = r[trust == "review"].groupby("h3").size().rename("n_unverified")
    r = r[trust == "accepted"].copy()
    if r.empty:
        return unverified.rename_axis("h3").reset_index().assign(n_reports=0)
    r["ordinal"] = _depth_ordinal(_col(r, "depth_class"))
    r["captured_at"] = pd.to_datetime(_col(r, "captured_at", None), errors="coerce", utc=True)
    for c in ("people_visible", "people_in_water", "people_on_roofs_or_elevated", "confidence", "location_confidence"):
        r[c] = pd.to_numeric(_col(r, c), errors="coerce")
    own_id = pd.Series(r.index.astype(str), index=r.index)
    image = _col(r, "image_id", None).astype(object)
    device = _col(r, "device_id", None).astype(object)
    r["device"] = device.where(device.notna(), image.where(image.notna(), own_id))
    r["assessed"] = _col(r, "assessed", False).fillna(False).astype(bool)

    r["people_claimed"] = r.people_in_water.fillna(0) + r.people_on_roofs_or_elevated.fillna(0)
    people_devices = r[r.people_claimed > 0].groupby("h3").device.nunique()
    people_assessed = r[r.assessed].groupby("h3").people_claimed.max()

    per_device = r.groupby(["h3", "device"]).ordinal.max().reset_index()
    median = per_device.groupby("h3").ordinal.median().apply(np.floor)
    per_device = per_device.join(median.rename("median"), on="h3")
    close = (per_device.ordinal - per_device["median"]).abs() <= 1
    agreeing = per_device[close].groupby("h3").device.nunique()
    assessed = r[r.assessed].groupby("h3").ordinal.max()

    g = r.groupby("h3")
    out = pd.DataFrame(
        {
            "n_reports": g.size(),
            "n_devices": g.device.nunique(),
            "n_with_depth": g.ordinal.count(),
            "depth_claim": median.map(_class_at),
            "depth_agreeing_devices": agreeing,
            "depth_assessed": assessed.map(_class_at),
            "people_devices": people_devices,
            "people_assessed": people_assessed,
            "people_visible": g.people_visible.max(),
            "people_in_water": g.people_in_water.max(),
            "people_elevated": g.people_on_roofs_or_elevated.max(),
            "assessment_confidence": g.confidence.mean(),
            "location_confidence": g.location_confidence.mean(),
            "first_report": g.captured_at.min(),
            "last_report": g.captured_at.max(),
        }
    )
    out = out.join(unverified, how="outer")
    for c in ("n_reports", "n_unverified", "depth_agreeing_devices", "n_devices", "n_with_depth", "people_devices"):
        out[c] = out[c].fillna(0).astype(int)
    return out.rename_axis("h3").reset_index()


def score(hexes: pd.DataFrame, weights: Weights | None = None) -> pd.DataFrame:
    """Add ``exposure``, ``hazard``, ``access``, ``priority``, ``confidence`` and the verification columns.

    Report claims count towards priority only when something independent of the reporters backs them:

    - a depth claim: satellite water in the hexagon or a neighbour (``satellite_water_near``), or, only where
      the satellite has not mapped the hexagon at all, at least two devices agreeing within one class;
    - a depth read from the photo by the model (``depth_assessed``) needs the same backing. On the depth benchmark
      it does no better than always answering the commonest class (``results/vlm_benchmark.json``), so it is a
      second opinion on the photo, not independent evidence;
    - a people claim: the model's own count (``people_assessed``), or two devices claiming people, again only
      where the satellite has not mapped the hexagon dry. Satellite water confirms water, never people.

    Device agreement cannot overrule a satellite map that says dry: that is a contradiction to verify, not
    evidence (``attack.py`` showed two colluding devices otherwise reach the top ten). Anything not backed sets
    ``needs_verification`` and a ``verify_score`` and leaves priority where the independent layers put it.
    One report, or two that agree, can send someone to look; they cannot send a boat into a mapped-dry street.
    """
    w = weights or Weights()
    h = hexes.copy()
    sat = _col(h, "satellite_water_fraction")
    cut_off = _col(h, "cut_off", False).fillna(False).astype(bool)
    in_flood = _col(h, "buildings_in_flood", 0).fillna(0)
    total = _col(h, "buildings_total", 0).fillna(0)

    claim = _col(h, "depth_claim", None).map(DEPTH_SEVERITY).astype(float)
    assessed = _col(h, "depth_assessed", None).map(DEPTH_SEVERITY).astype(float)
    claimed_people = _col(h, "people_in_water", 0).fillna(0) + _col(h, "people_elevated", 0).fillna(0)
    people_assessed = _col(h, "people_assessed").astype(float)
    sat_near = _col(h, "satellite_water_near", False).fillna(False).astype(bool)
    mapped_dry = sat.notna() & ~sat_near  # the satellite looked and saw no water here or next door
    if w.permissive_corroboration:
        mapped_dry = pd.Series(False, index=h.index)
    devices_agree = (_col(h, "depth_agreeing_devices", 0).fillna(0) >= CORROBORATING_DEVICES) & ~mapped_dry
    people_agree = (_col(h, "people_devices", 0).fillna(0) >= CORROBORATING_DEVICES) & ~mapped_dry
    depth_ok = sat_near | devices_agree
    assessed_ok = assessed.notna() & depth_ok
    people_ok = people_assessed.notna() | people_agree | (sat_near & w.permissive_corroboration)
    has_depth, has_people = claim.notna(), claimed_people > 0
    h["corroborated"] = (has_depth & depth_ok) | (has_people & people_ok)
    unbacked_model = assessed.notna() & ~assessed_ok
    h["needs_verification"] = (
        (has_depth & ~depth_ok)
        | (has_people & ~people_ok)
        | unbacked_model
        | (_col(h, "n_unverified", 0).fillna(0) > 0)
    )
    unbacked_depth = np.fmax(
        np.where(has_depth & ~depth_ok, claim.fillna(0), 0.0), np.where(unbacked_model, assessed.fillna(0), 0.0)
    )
    unbacked_people = np.where(has_people & ~people_ok, claimed_people, 0.0)
    h["verify_score"] = np.where(
        h.needs_verification, np.fmax(unbacked_depth, np.tanh(unbacked_people.astype(float) / 5.0)), 0.0
    ).round(3)
    report_severity = np.fmax(
        np.where(assessed_ok, assessed.to_numpy(float), np.nan), np.where(depth_ok, claim.to_numpy(float), np.nan)
    )
    people = np.where(people_ok, np.where(people_assessed.notna(), people_assessed, claimed_people), 0)

    affected = in_flood + np.where(cut_off, np.maximum(total - in_flood, 0), 0) + w.person_weight * people
    h["buildings_affected"] = (in_flood + np.where(cut_off, np.maximum(total - in_flood, 0), 0)).astype(int)
    h["exposure"] = 1.0 - np.exp(-affected / w.exposure_scale)
    h["hazard"] = pd.Series(np.fmax(report_severity, sat.to_numpy(dtype=float)), index=h.index).fillna(0.0).clip(0, 1)
    delay = _col(h, "delay_min").fillna(0).clip(lower=0)
    h["access"] = np.where(cut_off, 1.0, (delay / w.delay_saturation_min).clip(upper=1.0))
    gate = (h.hazard > 0) | cut_off
    h["priority"] = (gate * (w.exposure * h.exposure + w.hazard * h.hazard + w.access * h.access)).clip(0, 1)

    # One third per independent layer: satellite (per hex, may be unmapped), reach and exposure (per run).
    layers_known = (sat.notna().astype(float) + float("reach_dry_min" in h) + float("buildings_in_flood" in h)) / 3.0
    devices = _col(h, "n_devices", None).astype(float).fillna(_col(h, "n_reports", 0).astype(float)).fillna(0)
    photo = np.tanh(devices / 2.0) * _col(h, "location_confidence", 0).fillna(0)
    h["confidence"] = (0.6 * layers_known + 0.4 * photo).clip(0, 1)
    if "depth_claim" in h:
        h["photo_satellite_agreement"] = np.where(claim.notna() & sat.notna(), 1.0 - (claim - sat).abs(), np.nan)
    for c in ("exposure", "hazard", "access", "priority", "confidence"):
        h[c] = h[c].round(3)
    return h.sort_values("priority", ascending=False).reset_index(drop=True)


def satellite_water_near(cells: pd.Series, water: pd.Series, k: int = 1) -> pd.Series:
    """True where the satellite maps water in the hexagon or within ``k`` rings of it."""
    import h3

    wet = set(cells[water.fillna(0).to_numpy() > 0])
    return cells.map(lambda c: any(n in wet for n in h3.grid_disk(c, k)))


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
    if "satellite_water_fraction" in base:
        base["satellite_water_near"] = satellite_water_near(base.h3, base.satellite_water_fraction)
    if reports is not None and len(reports) and len(base):
        rep = aggregate(reports, h3.get_resolution(base.h3.iloc[0]))
        base = base.merge(rep, on="h3", how="left")
        for c in ("n_reports", "n_unverified"):
            if c in base:
                base[c] = base[c].fillna(0).astype(int)
    return base


def run(paths: Paths, layer_csvs: list[Path], reports_csv: Path | None = None, out_stem: str = "hexes") -> pd.DataFrame:
    manifest = RunManifest(
        "fuse", params={"layers": [str(p) for p in layer_csvs], "reports": str(reports_csv) if reports_csv else None}
    )
    reports = pd.read_csv(reports_csv) if reports_csv is not None else None
    ranked = score(combine([pd.read_csv(p) for p in layer_csvs], reports))
    csv = paths.results / f"{out_stem}.csv"
    ranked.to_csv(csv, index=False)
    if "needs_verification" in ranked:
        # The coordinator's check list: claims nothing independent backs yet, most serious first.
        verify = ranked[ranked.needs_verification].sort_values("verify_score", ascending=False)
        cols = [
            c
            for c in (
                "h3",
                "verify_score",
                "depth_claim",
                "people_in_water",
                "people_elevated",
                "n_reports",
                "n_unverified",
                "n_devices",
                "satellite_water_fraction",
            )
            if c in verify
        ]
        verify[cols].to_csv(paths.results / f"{out_stem}_verify.csv", index=False)
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
        "hexes_needing_verification": int(ranked.get("needs_verification", pd.Series(False)).sum()),
        "top10": live.head(10)[["h3", "priority", "confidence", "buildings_affected"]].to_dict("records"),
    }
    manifest.finish(**summary).write(paths.results / f"{out_stem}_run.json")
    log.info("fuse %s: %d hexes, %d with priority > 0 -> %s", out_stem, len(ranked), len(live), csv)
    return ranked
