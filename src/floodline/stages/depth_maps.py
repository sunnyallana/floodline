"""Stage 2: metric depth maps (Depth Anything V2) for the evaluation set and the depth benchmark.

Torch and transformers are imported inside ``run`` so the package stays importable without them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from PIL import Image

from floodline import datasets
from floodline.config import ModelIds, Paths, default_threads
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)


def targets(paths: Paths, limit: int | None = None, dataset: str = "cvfd") -> tuple[datasets.Dataset, pd.DataFrame]:
    """Eval images plus the depth-benchmark images, de-duplicated."""
    ds = datasets.load(paths, dataset)
    frames = [ds.frame[["image_id", "image_path"]]]
    if dataset == "cvfd" and paths.depth_labels.exists():
        frames.append(pd.read_csv(paths.depth_labels, dtype=str)[["image_id", "image_path"]])
    df = pd.concat(frames).drop_duplicates("image_id").reset_index(drop=True)
    return ds, (df.head(limit) if limit else df)


def run(
    paths: Paths,
    model: str = ModelIds.depth,
    limit: int | None = None,
    threads: int | None = None,
    dataset: str = "cvfd",
) -> int:
    import torch
    from transformers import pipeline

    threads = threads or default_threads()
    torch.set_num_threads(threads)
    ds, todo = targets(paths, limit, dataset)
    paths.depth_maps.mkdir(parents=True, exist_ok=True)
    manifest = RunManifest("depth", params={"model": model, "threads": threads, "device": "cpu", "targets": len(todo)})

    pipe = pipeline("depth-estimation", model=model, device=-1)
    new = 0
    for _, r in todo.iterrows():
        out = paths.depth_maps / f"{r.image_id}.npz"
        if out.exists():
            continue
        depth = pipe(Image.open(ds.image(r)).convert("RGB"))["predicted_depth"]
        np.savez_compressed(out, depth_m=np.asarray(depth, dtype=np.float16))
        new += 1
        if new % 50 == 0:
            log.info("%d new depth maps", new)
    total = len(list(paths.depth_maps.glob("*.npz")))
    manifest.finish(
        images_new=new, images_total=total, s_per_image=round((manifest.seconds or 0.0) / new, 3) if new else None
    )
    manifest.write(paths.results / "depth_run.json")
    log.info("%d new maps, %d total -> %s", new, total, paths.depth_maps)
    return new
