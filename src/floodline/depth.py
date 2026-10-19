"""Flood depth from reference objects.

Given a water mask and an instance mask of an object with a known real-world height, the fraction of the
object below the waterline gives a depth estimate. Several objects in one photo give several estimates; we
report the median and the spread, and bin the result into classes a rescue coordinator understands.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Typical heights in centimetres. Ranges are deliberately wide; the bin boundaries absorb the uncertainty.
REFERENCE_HEIGHTS_CM: dict[str, tuple[float, float]] = {
    "person": (150, 180),  # standing adult, head to ground
    "car": (140, 160),  # roof height of a sedan / small hatchback
    "car wheel": (55, 70),  # tyre diameter
    "motorbike": (100, 120),  # handlebar height
    "door": (195, 215),
    "window": (90, 110),  # sill height above ground, typical ground floor
    "rickshaw": (165, 185),
    "bus": (300, 340),
}

DEPTH_BINS_CM = [
    ("dry", 0, 2),
    ("ankle", 2, 20),
    ("knee", 20, 50),
    ("waist", 50, 100),
    ("chest", 100, 150),
    ("above_head", 150, 10_000),
]


@dataclass
class DepthEstimate:
    object_class: str
    submerged_fraction: float
    depth_cm_low: float
    depth_cm_high: float

    @property
    def depth_cm_mid(self) -> float:
        return 0.5 * (self.depth_cm_low + self.depth_cm_high)


def submerged_fraction(object_mask: np.ndarray, water_mask: np.ndarray) -> float:
    """Fraction of the object's vertical extent that lies below the waterline.

    The waterline is taken as the highest row (smallest y) where water touches the object's columns inside the
    object's bounding box. Photos are assumed roughly upright.
    """
    ys, xs = np.nonzero(object_mask)
    if ys.size == 0:
        return 0.0
    top, bottom = ys.min(), ys.max()
    height = bottom - top + 1
    if height < 4:
        return 0.0
    # water pixels adjacent to the object within its bbox columns
    band = water_mask[top : bottom + 1, xs.min() : xs.max() + 1]
    rows = np.nonzero(band.any(axis=1))[0]
    if rows.size == 0:
        return 0.0
    waterline = rows.min()  # row index within bbox
    return float((height - waterline) / height)


def estimate_from_object(object_class: str, frac: float) -> DepthEstimate | None:
    if object_class not in REFERENCE_HEIGHTS_CM:
        return None
    lo, hi = REFERENCE_HEIGHTS_CM[object_class]
    return DepthEstimate(object_class, frac, lo * frac, hi * frac)


def bin_depth(depth_cm: float) -> str:
    for name, lo, hi in DEPTH_BINS_CM:
        if lo <= depth_cm < hi:
            return name
    return DEPTH_BINS_CM[-1][0]


def combine(estimates: list[DepthEstimate]) -> dict:
    """Median of the per-object midpoints, with the spread as a crude confidence."""
    if not estimates:
        return {"depth_cm": None, "depth_bin": None, "n_refs": 0, "spread_cm": None}
    mids = np.array([e.depth_cm_mid for e in estimates])
    med = float(np.median(mids))
    return {
        "depth_cm": med,
        "depth_bin": bin_depth(med),
        "n_refs": len(estimates),
        "spread_cm": float(mids.max() - mids.min()) if len(mids) > 1 else 0.0,
    }
