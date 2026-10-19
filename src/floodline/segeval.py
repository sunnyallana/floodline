"""Binary mask metrics for scoring zero-shot segmentation against the CVFD masks."""

from __future__ import annotations

import numpy as np
from PIL import Image


def load_mask(path, size: tuple[int, int] | None = None) -> np.ndarray:
    """Load a mask as a boolean array. Any non-zero pixel counts as foreground."""
    im = Image.open(path).convert("L")
    if size is not None and im.size != size:
        im = im.resize(size, Image.NEAREST)
    return np.asarray(im) > 0


def iou(pred: np.ndarray, gt: np.ndarray) -> float:
    inter = np.logical_and(pred, gt).sum()
    union = np.logical_or(pred, gt).sum()
    return float(inter / union) if union else 1.0


def dice(pred: np.ndarray, gt: np.ndarray) -> float:
    inter = np.logical_and(pred, gt).sum()
    denom = pred.sum() + gt.sum()
    return float(2 * inter / denom) if denom else 1.0


def boundary_f1(pred: np.ndarray, gt: np.ndarray, tol: int = 2) -> float:
    """F1 between mask boundaries within `tol` pixels. Cheap morphological version, no SciPy needed."""

    def edge(m):
        m = m.astype(np.uint8)
        pad = np.pad(m, 1)
        er = pad[1:-1, 1:-1] & pad[:-2, 1:-1] & pad[2:, 1:-1] & pad[1:-1, :-2] & pad[1:-1, 2:]
        return m.astype(bool) & ~er.astype(bool)

    def dilate(m, r):
        out = m.copy()
        for _ in range(r):
            pad = np.pad(out, 1)
            out = pad[1:-1, 1:-1] | pad[:-2, 1:-1] | pad[2:, 1:-1] | pad[1:-1, :-2] | pad[1:-1, 2:]
        return out

    ep, eg = edge(pred), edge(gt)
    if not ep.any() and not eg.any():
        return 1.0
    if not ep.any() or not eg.any():
        return 0.0
    prec = (ep & dilate(eg, tol)).sum() / ep.sum()
    rec = (eg & dilate(ep, tol)).sum() / eg.sum()
    return float(2 * prec * rec / (prec + rec)) if prec + rec else 0.0


def score_pair(pred: np.ndarray, gt: np.ndarray) -> dict:
    return {"iou": iou(pred, gt), "dice": dice(pred, gt), "boundary_f1": boundary_f1(pred, gt)}
