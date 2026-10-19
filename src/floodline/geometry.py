"""Waterline geometry: depth from a reference object's visible silhouette.

Two facts drive the design, both learned from the first benchmark pass:

1. A segmentation mask of an object standing in water only covers the part *above* the surface, so the mask's
   bottom edge is the waterline. The submerged fraction cannot be read from the mask itself; it has to be inferred
   from how much of the object's full height is visible, using proportions that are stable for that object class
   (a standing adult is about four shoulder-widths tall; a door is about 2.2 times as tall as it is wide).
2. Water that is visible *behind* an object sits higher in the image than the object's base. Image rows cannot
   tell near water from far water, but the metric depth map can: water touching an object is at the object's
   distance from the camera, water behind it is farther.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Full height of the object as a multiple of a dimension that stays visible above the water.
STATURE_FROM_WIDTH: dict[str, tuple[str, float]] = {
    "person": ("shoulder_width", 4.0),  # adult stature ≈ 4 × biacromial width (with clothing)
    "door": ("width", 2.2),  # 0.9 m wide, 2.0 m tall
    "car": ("length", 0.45),  # side view: height ≈ 0.45 × length
    "motorbike": ("length", 0.55),
}

TOUCH_BAND_PX = 4  # how far below and beside the object's base to look for water
DEPTH_TOLERANCE = 0.30  # object vs touching-water distance may differ by this fraction


@dataclass(frozen=True)
class Silhouette:
    top: int
    bottom: int
    left: int
    right: int
    height_px: int
    width_px: int
    shoulder_width_px: int  # widest row in the upper third (people); same as width for other classes


def silhouette(mask: np.ndarray) -> Silhouette | None:
    ys, xs = np.nonzero(mask)
    if ys.size == 0:
        return None
    top, bottom, left, right = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    height = bottom - top + 1
    upper = mask[top : top + max(1, height // 3)]
    shoulder = int(upper.sum(axis=1).max()) if upper.size else right - left + 1
    return Silhouette(top, bottom, left, right, height, right - left + 1, shoulder)


def touches_water(mask: np.ndarray, water: np.ndarray, depth_m: np.ndarray | None, band: int = TOUCH_BAND_PX) -> bool:
    """Is there water just below or beside the object's base, at the object's distance from the camera?"""
    sil = silhouette(mask)
    if sil is None:
        return False
    h, w = mask.shape
    y0 = max(sil.bottom - band, 0)
    y1 = min(sil.bottom + band, h - 1)
    x0 = max(sil.left - band, 0)
    x1 = min(sil.right + band, w - 1)
    region = np.zeros_like(mask)
    region[y0 : y1 + 1, x0 : x1 + 1] = True
    candidate = region & water & ~mask
    if candidate.sum() < band * 2:
        return False
    if depth_m is None:
        return True
    base_rows = mask[max(sil.bottom - 3, 0) : sil.bottom + 1]
    base = np.zeros_like(mask)
    base[max(sil.bottom - 3, 0) : sil.bottom + 1] = base_rows
    d_obj = float(np.median(depth_m[base])) if base.any() else float(np.median(depth_m[mask]))
    d_water = float(np.median(depth_m[candidate]))
    return abs(d_obj - d_water) <= DEPTH_TOLERANCE * max(d_obj, d_water, 1e-6)


def visible_fraction(ref_class: str, sil: Silhouette) -> float | None:
    """Fraction of the object's full height that is above the waterline, from class proportions. ``None`` if
    the class has no usable proportion or the silhouette is too small to measure."""
    if ref_class not in STATURE_FROM_WIDTH or sil.height_px < 6 or sil.width_px < 3:
        return None
    dim, k = STATURE_FROM_WIDTH[ref_class]
    width = sil.shoulder_width_px if dim == "shoulder_width" else sil.width_px
    full_height_px = k * width
    if full_height_px <= 0:
        return None
    return float(min(1.0, sil.height_px / full_height_px))


def submerged_fraction_from_visible(visible: float) -> float:
    return float(max(0.0, 1.0 - visible))
