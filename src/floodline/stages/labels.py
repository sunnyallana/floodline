"""Human review of the depth benchmark: agreement with the first-pass labels, and the adjudicated reference.

The first-pass labels (``data/depth_labels.csv``) were assigned with an AI assistant. A person then labels the
same 300 photos blind, in shuffled order, with ``docs/review/`` and settles every disagreement after seeing both
answers. This stage reads that export and writes:

- ``results/label_agreement.json``: raw agreement, Cohen's kappa, and linearly weighted kappa over the ordered
  classes (dry < ankle < knee < waist < chest < above_head), with "unknown" scored as its own nominal class in
  the unweighted kappa and excluded from the weighted one; the confusion matrix;
- ``data/depth_labels_reviewed.csv``: the benchmark with the adjudicated class as ``depth_bin`` and the first
  pass kept as ``depth_bin_first_pass``.

Every benchmark score is then recomputed against the reviewed labels.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

ORDERED: tuple[str, ...] = ("dry", "ankle", "knee", "waist", "chest", "above_head")
CLASSES: tuple[str, ...] = (*ORDERED, "unknown")


def cohen_kappa(a: pd.Series, b: pd.Series, classes: tuple[str, ...], weighted: bool = False) -> float:
    """Cohen's kappa; with ``weighted``, linear weights by class distance (classes must be ordered)."""
    k = len(classes)
    idx = {c: i for i, c in enumerate(classes)}
    keep = a.isin(classes) & b.isin(classes)
    if keep.sum() == 0:
        return float("nan")
    m = np.zeros((k, k))
    for x, y in zip(a[keep].map(idx), b[keep].map(idx), strict=True):
        m[x, y] += 1
    m /= m.sum()
    expected = np.outer(m.sum(axis=1), m.sum(axis=0))
    i, j = np.indices((k, k))
    w = np.abs(i - j) / (k - 1) if weighted else (i != j).astype(float)
    denom = float((w * expected).sum())
    return float(1 - (w * m).sum() / denom) if denom else float("nan")


def agreement(first: pd.Series, human: pd.Series) -> dict[str, Any]:
    both = first.isin(CLASSES) & human.isin(CLASSES)
    f, h = first[both], human[both]
    ordered = f.isin(ORDERED) & h.isin(ORDERED)
    return {
        "n": int(both.sum()),
        "raw_agreement": round(float((f == h).mean()), 3),
        "kappa": round(cohen_kappa(f, h, CLASSES), 3),
        "weighted_kappa_ordered": round(cohen_kappa(f[ordered], h[ordered], ORDERED, weighted=True), 3),
        "n_ordered_both": int(ordered.sum()),
        "unknown_first_pass_only": int(((f == "unknown") & (h != "unknown")).sum()),
        "unknown_human_only": int(((h == "unknown") & (f != "unknown")).sum()),
        "confusion": pd.crosstab(f, h).reindex(index=list(CLASSES), columns=list(CLASSES), fill_value=0).to_dict(),
    }


def run(paths: Paths, review_csv: Path) -> dict[str, Any]:
    first = pd.read_csv(paths.depth_labels)
    review = pd.read_csv(review_csv)
    merged = first.merge(review, on="image_id", how="inner", validate="one_to_one")
    if len(merged) < len(first):
        log.warning("review covers %d of %d benchmark images", len(merged), len(first))
    summary = {
        "blind_vs_first_pass": agreement(merged.depth_bin, merged.human_depth_bin),
        "final_vs_first_pass": agreement(merged.depth_bin, merged.final_depth_bin),
        "adjudicated": int(merged.adjudicated.astype(str).str.lower().eq("true").sum()),
        "final_class_counts": merged.final_depth_bin.value_counts().to_dict(),
    }
    reviewed = merged.rename(columns={"depth_bin": "depth_bin_first_pass", "final_depth_bin": "depth_bin"})
    cols = [
        "image_id",
        "split",
        "image_path",
        "depth_bin",
        "depth_bin_first_pass",
        "human_depth_bin",
        "human_reference",
        "reference",
        "note",
        "human_note",
    ]
    reviewed[[c for c in cols if c in reviewed]].to_csv(paths.data / "depth_labels_reviewed.csv", index=False)
    RunManifest("label_agreement", params={"review": str(review_csv)}).finish(**summary).write(
        paths.results / "label_agreement.json"
    )
    log.info("label agreement: %s", {k: v for k, v in summary["blind_vs_first_pass"].items() if k != "confusion"})
    return summary
