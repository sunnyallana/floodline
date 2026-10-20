"""Stage 1: zero-shot concept segmentation with SAM 3, scored against the CVFD masks.

For each image and text prompt the accepted instances are saved as one label image (0 = background, k = instance
k) and the union is scored against the hand-reviewed mask where one exists. The run is resumable: images already
present in the scores CSV are skipped, and the CSV is rewritten atomically every ``flush_every`` images.
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image

from floodline import datasets
from floodline.config import SAM3_GROUND_TRUTH, SAM3_PROMPTS, ModelIds, Paths, default_threads
from floodline.cvfd import load_index
from floodline.log import get_logger
from floodline.resume import CsvStore
from floodline.runs import RunManifest
from floodline.segeval import load_mask, score_pair

log = get_logger(__name__)

OVERLAY_COLOURS: dict[str, tuple[int, int, int]] = {
    "water": (40, 110, 255),
    "person": (255, 40, 40),
    "building": (40, 200, 90),
    "car": (255, 180, 0),
    "motorbike": (200, 0, 200),
    "door": (0, 200, 200),
    "window": (255, 255, 0),
}


@dataclass
class Segmentation:
    n_instances: int
    union: np.ndarray  # bool, HxW
    labels: np.ndarray  # uint8, HxW, 0 background


class Sam3:
    """Thin wrapper around the transformers SAM 3 classes, tolerant of post-processing API differences."""

    def __init__(self, model_id: str = ModelIds.sam3, threshold: float = 0.5) -> None:
        import torch
        import transformers

        if not (hasattr(transformers, "Sam3Model") and hasattr(transformers, "Sam3Processor")):
            available = [n for n in dir(transformers) if n.lower().startswith("sam")]
            raise RuntimeError(f"transformers {transformers.__version__} has no Sam3 classes; found {available}")
        token = os.environ.get("HF_TOKEN")
        self.threshold = threshold
        self.processor = transformers.Sam3Processor.from_pretrained(model_id, token=token)
        self.model = transformers.Sam3Model.from_pretrained(model_id, token=token, torch_dtype=torch.float32).eval()
        self._torch = torch

    def segment(self, image: Image.Image, prompt: str) -> Segmentation:
        inputs = self.processor(images=image, text=prompt, return_tensors="pt")
        with self._torch.no_grad():
            outputs = self.model(**inputs)
        return self._postprocess(outputs, image.size[::-1])

    def _postprocess(self, outputs: Any, size_hw: tuple[int, int]) -> Segmentation:
        for name in ("post_process_instance_segmentation", "post_process_masks"):
            fn = getattr(self.processor, name, None)
            if fn is None:
                continue
            params = inspect.signature(fn).parameters
            kwargs: dict[str, Any] = {}
            if "threshold" in params:
                kwargs["threshold"] = self.threshold
            if "mask_threshold" in params:
                kwargs["mask_threshold"] = 0.5
            if "target_sizes" in params:
                kwargs["target_sizes"] = [size_hw]
            try:
                res = fn(outputs, **kwargs)
            except TypeError:
                res = fn(outputs)
            first = res[0] if isinstance(res, (list, tuple)) else res
            masks = first.get("masks") if isinstance(first, dict) else first
            return _to_segmentation(masks, size_hw)
        raise RuntimeError("Sam3Processor has no known post-processing method")


def _to_segmentation(masks: Any, size_hw: tuple[int, int]) -> Segmentation:
    empty = Segmentation(0, np.zeros(size_hw, bool), np.zeros(size_hw, np.uint8))
    if masks is None:
        return empty
    m = masks.detach().cpu().numpy() if hasattr(masks, "detach") else np.asarray(masks)
    if m.size == 0:
        return empty
    m = m.reshape(-1, *m.shape[-2:])
    m = m if m.dtype == bool else m > 0.5
    if m.shape[-2:] != tuple(size_hw):
        m = np.stack(
            [
                np.asarray(Image.fromarray(x.astype(np.uint8) * 255).resize(size_hw[::-1], Image.Resampling.NEAREST))
                > 0
                for x in m
            ]
        )
    labels = np.zeros(size_hw, np.uint8)
    for i, inst in enumerate(m[:255]):
        labels[inst & (labels == 0)] = i + 1
    return Segmentation(len(m), m.any(axis=0), labels)


def run(
    paths: Paths,
    dataset: str = "cvfd",
    prompts: Sequence[str] = SAM3_PROMPTS,
    limit: int | None = None,
    threshold: float = 0.5,
    threads: int | None = None,
    flush_every: int = 10,
) -> pd.DataFrame:
    import torch

    threads = threads or default_threads()
    torch.set_num_threads(threads)
    ds = datasets.load(paths, dataset, limit=limit)
    ev = ds.frame
    paths.sam3_masks.mkdir(parents=True, exist_ok=True)
    store = CsvStore(paths.sam3_scores)
    done = store.done()
    manifest = RunManifest(
        "segment",
        params={
            "model": ModelIds.sam3,
            "prompts": list(prompts),
            "threshold": threshold,
            "threads": threads,
            "device": "cpu",
        },
    )
    sam = Sam3(threshold=threshold)
    pending: list[dict[str, Any]] = []
    calls = 0
    for _, r in ev.iterrows():
        if r.image_id in done:
            continue
        image = Image.open(ds.image(r)).convert("RGB")
        rec: dict[str, Any] = {"image_id": r.image_id}
        for prompt in prompts:
            seg = sam.segment(image, prompt)
            calls += 1
            rec[f"n_{prompt}"] = seg.n_instances
            rec[f"area_{prompt}"] = round(float(seg.union.mean()), 5)
            Image.fromarray(seg.labels).save(paths.sam3_masks / f"{r.image_id}__{prompt}.png")
            gt_col = SAM3_GROUND_TRUTH.get(prompt)
            if ds.has_masks and gt_col and isinstance(r.get(gt_col), str):
                gt = load_mask(ds.root / r[gt_col], size=image.size)
                rec.update({f"{prompt}_{k}": round(v, 5) for k, v in score_pair(seg.union, gt).items()})
        pending.append(rec)
        if len(pending) >= flush_every:
            store.extend(pending)
            pending = []
            log.info("%d images scored, %.1f s/call", len(store.done()), manifest.elapsed / max(calls, 1))
    store.extend(pending)
    scores = pd.DataFrame(store.rows())
    manifest.finish(images=len(scores), calls=calls, summary=summarise_scores(scores))
    manifest.write(paths.results / "sam3_run.json")
    return scores


def summarise_scores(scores: pd.DataFrame) -> dict[str, dict[str, float]]:
    cols = [c for c in scores.columns if c.endswith(("_iou", "_dice", "_boundary_f1"))]
    if not cols:
        return {}
    num = scores[cols].apply(pd.to_numeric, errors="coerce")
    return {
        c: {
            "mean": round(float(num[c].mean()), 4),
            "median": round(float(num[c].median()), 4),
            "n": int(num[c].notna().sum()),
        }
        for c in cols
    }


def overlay(paths: Paths, image_ids: Sequence[str], out: Path | None = None, scale: int = 2) -> Path:
    """Original | SAM 3 overlay | CVFD flood mask, one row per image. For eyeballing, and for the write-up."""
    index = load_index(paths.cvfd_index).set_index("image_id")
    rows = []
    for iid in image_ids:
        img = np.asarray(Image.open(paths.cvfd / index.loc[iid, "image_path"]).convert("RGB")).astype(float)
        pred = img.copy()
        for prompt, colour in OVERLAY_COLOURS.items():
            mask_path = paths.sam3_masks / f"{iid}__{prompt}.png"
            if mask_path.exists():
                m = np.asarray(Image.open(mask_path)) > 0
                pred[m] = 0.45 * pred[m] + 0.55 * np.array(colour)
        gt_img = img.copy()
        gt_rel = index.loc[iid, "mask_flood"]
        if isinstance(gt_rel, str):
            gt = load_mask(paths.cvfd / gt_rel)
            gt_img[gt] = 0.45 * gt_img[gt] + 0.55 * np.array(OVERLAY_COLOURS["water"])
        rows.append(np.concatenate([img, pred, gt_img], axis=1))
    sheet = np.concatenate(rows, axis=0).astype(np.uint8)
    out = out or paths.results / "sam3_preview.png"
    Image.fromarray(sheet).resize((sheet.shape[1] * scale, sheet.shape[0] * scale), Image.Resampling.NEAREST).save(out)
    return out
