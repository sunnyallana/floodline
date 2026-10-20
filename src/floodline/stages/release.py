"""Package the flood-depth benchmark as a Hugging Face dataset and, with a write token, publish it.

The bundle is an ``imagefolder`` dataset: ``images/<image_id>.jpg`` plus ``metadata.csv`` (one row per image,
``file_name`` first, as the loader expects) and a dataset card. ``datasets.load_dataset("<repo>")`` then yields
the image with its depth class, reference object and note.

A DOI is minted from the dataset's settings page on the Hub after the first upload (there is no API for it); the
card's citation block is written so the DOI can be added without changing anything else.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

DEFAULT_REPO = "sunnyallana/flood-depth-benchmark"
CLASS_ORDER = ("dry", "ankle", "knee", "waist", "chest", "above_head", "unknown")
# Update when a person has checked the labels; the card states it either way.
REVIEW_STATUS = "have not yet been checked by a second, human annotator"

CARD = """---
license: cc-by-sa-4.0
pretty_name: Flood Depth Benchmark
task_categories:
- image-classification
tags:
- flood
- disaster-response
- flood-depth
- benchmark
size_categories:
- n<1K
configs:
- config_name: default
  data_files:
  - split: test
    path: "images/**"
---

# Flood Depth Benchmark

{n} ground-level flood photos from the test split of the Close-View Flood Dataset (CVFD), each labelled with
how deep the water is against an object of known height. It is the depth benchmark of
[Floodline](https://github.com/sunnyallana/floodline), which estimates depth zero-shot from the waterline.

**How the labels were made.** Each image was labelled by visual inspection of contact sheets with an AI
assistant (Claude, Anthropic) during the Floodline project, against the class definitions below. The labels
{review}. Treat them as a first-pass reference, not as expert ground truth.

| Class | Meaning | Images |
|---|---|---|
{rows}

`reference` names the object the depth was read against (person, car, door, building, ...). `note` is the
labeller's free text; {low} rows say "low confidence", and `low_confidence` flags them.

## Why two thirds are `unknown`

Most web-sourced flood imagery has nothing standing in the water that could calibrate depth: aerial views,
rivers in flood, open water with no people, vehicles or doors in it. Those images are labelled `unknown` rather
than guessed. A method that never says `unknown` on this set is overconfident.

## Fields

| Field | Type | |
|---|---|---|
| `image` | image | 224 × 224 RGB, EXIF removed |
| `image_id` | string | CVFD id, e.g. `flooded_2272` |
| `depth_bin` | string | one of {classes} |
| `reference` | string | object the depth was read against, or empty |
| `note` | string | labeller's note |
| `low_confidence` | bool | the note says "low confidence" |

## Provenance and licence

Images come from CVFD (see its card): mostly FloodIMG (CC0) plus web-sourced scenes whose sources were not
recorded. The labels are original work. Released under CC BY-SA 4.0. If an image is yours and you want it
removed, open an issue at https://github.com/sunnyallana/floodline with the image id.

## Citation

```
@dataset{{shabanali2026flooddepth,
  author = {{Shaban Ali, Sunny}},
  title  = {{Flood Depth Benchmark}},
  year   = {{2026}},
  url    = {{https://huggingface.co/datasets/{repo}}}
}}
```
"""


def build(paths: Paths, out_dir: Path, repo: str = DEFAULT_REPO) -> pd.DataFrame:
    labels = pd.read_csv(paths.depth_labels)
    images = out_dir / "images"
    images.mkdir(parents=True, exist_ok=True)
    missing = []
    for row in labels.itertuples(index=False):
        src = paths.cvfd / row.image_path
        if not src.exists():
            missing.append(row.image_id)
            continue
        shutil.copy2(src, images / f"{row.image_id}.jpg")
    if missing:
        raise FileNotFoundError(f"{len(missing)} benchmark images not found under {paths.cvfd}: {missing[:5]}")
    note = labels.note.fillna("")
    meta = pd.DataFrame(
        {
            "file_name": [f"{i}.jpg" for i in labels.image_id],
            "image_id": labels.image_id,
            "depth_bin": labels.depth_bin,
            "reference": labels.reference.fillna(""),
            "note": note,
            "low_confidence": note.str.contains("low confidence", case=False),
        }
    )
    meta.to_csv(images / "metadata.csv", index=False)
    counts = meta.depth_bin.value_counts()
    meanings = {
        "dry": "under 2 cm at the reference",
        "ankle": "2-20 cm",
        "knee": "20-50 cm",
        "waist": "50-100 cm",
        "chest": "100-160 cm",
        "above_head": "160 cm or more, or ground floor submerged",
        "unknown": "no reference object, or aerial / river view",
    }
    rows = "\n".join(f"| `{c}` | {meanings[c]} | {int(counts.get(c, 0))} |" for c in CLASS_ORDER)
    (out_dir / "README.md").write_text(
        CARD.format(
            n=len(meta),
            rows=rows,
            low=int(meta.low_confidence.sum()),
            classes=", ".join(f"`{c}`" for c in CLASS_ORDER),
            repo=repo,
            review=REVIEW_STATUS,
        ),
        encoding="utf-8",
    )
    return meta


def run(paths: Paths, out_dir: Path | None = None, repo: str = DEFAULT_REPO, push: bool = False) -> Path:
    out_dir = out_dir or paths.data / "bundle" / "flood-depth-benchmark"
    manifest = RunManifest("release", params={"repo": repo, "push": push})
    meta = build(paths, out_dir, repo)
    url = None
    if push:
        from huggingface_hub import HfApi

        token = os.environ.get("HF_WRITE_TOKEN") or os.environ.get("HF_TOKEN")
        if not token:
            raise RuntimeError("set HF_WRITE_TOKEN to a token with write access to publish")
        api = HfApi(token=token)
        api.create_repo(repo, repo_type="dataset", exist_ok=True)
        api.upload_folder(folder_path=str(out_dir), repo_id=repo, repo_type="dataset", commit_message="Depth benchmark")
        url = f"https://huggingface.co/datasets/{repo}"
    manifest.finish(images=len(meta), classes=meta.depth_bin.value_counts().to_dict(), url=url).write(
        paths.results / "release_run.json"
    )
    log.info("bundle %s (%d images)%s", out_dir, len(meta), f" -> {url}" if url else "; not pushed")
    return out_dir
