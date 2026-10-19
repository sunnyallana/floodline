"""Stage 2b: the waterline method. Flood depth from geometry alone: SAM 3 instance masks + metric depth.

For every reference object (person, car, motorbike, door, window) the fraction of its height below the waterline
gives a depth estimate from its known real-world height. The metric depth map is used as a consistency check:
an object whose distance from the camera differs a lot from the water next to it (a person on a far rooftop, a car
on a bridge) is not standing in that water and is skipped. This path needs no language model, so it is the one
that can run offline; the VLM path is scored against the same benchmark.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image

from floodline.config import Paths
from floodline.cvfd import eval_split, load_index
from floodline.depth import REFERENCE_HEIGHTS_CM, DepthEstimate, combine
from floodline.geometry import silhouette, submerged_fraction_from_visible, touches_water, visible_fraction
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

# Prompt name -> (reference class in REFERENCE_HEIGHTS_CM, height of the object's base above ground in cm).
# A window's mask starts at the sill, so water touching a window is already sill-deep.
REFERENCES: dict[str, tuple[str, float]] = {
    "person": ("person", 0.0),
    "car": ("car", 0.0),
    "motorbike": ("motorbike", 0.0),
    "door": ("door", 0.0),
    "window": ("window", 100.0),
}
MIN_OBJECT_PIXELS = 40
DRY_MAX_WATER_FRACTION = 0.15  # above this much water in frame, "no object in the water" is unknown, not dry


@dataclass
class ObjectReading:
    prompt: str
    instance: int
    submerged_fraction: float
    estimate: DepthEstimate | None
    consistent: bool


def instances(label_png: Path) -> list[np.ndarray]:
    labels = np.asarray(Image.open(label_png))
    return [labels == k for k in range(1, int(labels.max()) + 1) if (labels == k).sum() >= MIN_OBJECT_PIXELS]


def estimate_with_offset(ref_class: str, frac: float, base_cm: float) -> DepthEstimate | None:
    if frac <= 0 or ref_class not in REFERENCE_HEIGHTS_CM:
        return None
    lo, hi = REFERENCE_HEIGHTS_CM[ref_class]
    return DepthEstimate(ref_class, frac, base_cm + lo * frac, base_cm + hi * frac)


def read_image(paths: Paths, image_id: str, depth_map: np.ndarray | None) -> tuple[list[ObjectReading], dict[str, Any]]:
    water_png = paths.sam3_masks / f"{image_id}__water.png"
    if not water_png.exists():
        raise FileNotFoundError(water_png)
    water = np.asarray(Image.open(water_png)) > 0
    readings: list[ObjectReading] = []
    for prompt, (ref_class, base_cm) in REFERENCES.items():
        png = paths.sam3_masks / f"{image_id}__{prompt}.png"
        if not png.exists():
            continue
        for k, obj in enumerate(instances(png), start=1):
            sil = silhouette(obj)
            in_water = touches_water(obj, water, depth_map)
            vis = visible_fraction(ref_class, sil) if (sil is not None and in_water) else None
            frac = submerged_fraction_from_visible(vis) if vis is not None else 0.0
            est = estimate_with_offset(ref_class, frac, base_cm) if (in_water and vis is not None) else None
            readings.append(ObjectReading(prompt, k, frac, est, in_water))
    usable = [r.estimate for r in readings if r.consistent and r.estimate is not None]
    touched = any(r.consistent for r in readings)
    summary = combine(usable)
    water_fraction = float(water.mean())
    if not usable:
        # Reference objects present but none in the water, and little water in frame -> dry at those objects.
        # Lots of water but no object standing in it -> depth is unknown, not dry.
        summary["depth_bin"] = "dry" if readings and not touched and water_fraction < DRY_MAX_WATER_FRACTION else None
    summary.update(
        water_fraction=round(water_fraction, 4),
        n_objects=len(readings),
        n_consistent=sum(r.consistent for r in readings),
    )
    return readings, summary


def run(paths: Paths, limit: int | None = None) -> pd.DataFrame:
    ev = eval_split(load_index(paths.cvfd_index), limit=limit)
    labels = pd.read_csv(paths.depth_labels, dtype=str).fillna("") if paths.depth_labels.exists() else None
    manifest = RunManifest(
        "waterline", params={"references": REFERENCES, "method": "visible-fraction + depth-consistent touch"}
    )
    rows: list[dict[str, Any]] = []
    for _, r in ev.iterrows():
        npz = paths.depth_maps / f"{r.image_id}.npz"
        depth_map = np.load(npz)["depth_m"].astype(np.float32) if npz.exists() else None
        try:
            _, summary = read_image(paths, r.image_id, depth_map)
        except FileNotFoundError:
            continue  # SAM 3 has not reached this image yet; the run is re-entrant
        rows.append({"image_id": r.image_id, **summary})
    df = pd.DataFrame(rows)
    out = paths.results / "waterline_depth.csv"
    df.to_csv(out, index=False)
    metrics: dict[str, Any] = {"images": len(df), "with_estimate": int(df.depth_bin.notna().sum()) if len(df) else 0}
    if labels is not None and len(df):
        metrics["benchmark"] = score_against_labels(df, labels)
    manifest.finish(**metrics).write(paths.results / "waterline_run.json")
    log.info("waterline depth for %d images -> %s | %s", len(df), out, metrics)
    return df


ORDER = ["dry", "ankle", "knee", "waist", "chest", "above_head"]


def score_against_labels(pred: pd.DataFrame, labels: pd.DataFrame) -> dict[str, Any]:
    """Exact and within-one-bin agreement on the hand-labelled images that have a geometric estimate."""
    lab = labels[labels.depth_bin.isin(ORDER)][["image_id", "depth_bin"]].rename(columns={"depth_bin": "label"})
    m = pred.merge(lab, on="image_id")
    m = m[m.depth_bin.isin(ORDER)]
    if m.empty:
        return {"n": 0}
    idx = {b: i for i, b in enumerate(ORDER)}
    diff = (m.depth_bin.map(idx) - m.label.map(idx)).abs()
    return {
        "n": len(m),
        "exact": round(float((diff == 0).mean()), 4),
        "within_one": round(float((diff <= 1).mean()), 4),
        "mean_abs_bins": round(float(diff.mean()), 3),
    }
