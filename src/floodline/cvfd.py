"""Index of the Close-View Flood Dataset (CVFD) as it sits on disk.

Layout::

    images/{train,val,test}/{flooded,non-flooded}/*.jpg
    masks/{flood,buildings,humans}/{train,val,test}/flooded/*.png    (masks exist only for flooded images)

The index is one row per image with the relative path of each mask class when it exists. Every stage reads
images through this index rather than globbing the folders again, so splits and mask coverage are decided once.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

SPLITS: tuple[str, ...] = ("train", "val", "test")
LABELS: tuple[str, ...] = ("flooded", "non-flooded")
MASK_CLASSES: tuple[str, ...] = ("flood", "humans", "buildings")


def index_cvfd(root: Path) -> pd.DataFrame:
    """Build the index. Paths are stored relative to ``root`` so the parquet is portable between machines."""
    if not (root / "images").is_dir():
        raise FileNotFoundError(f"CVFD images folder not found under {root}")
    rows: list[dict[str, object]] = []
    for split in SPLITS:
        for label in LABELS:
            for img in sorted((root / "images" / split / label).glob("*.jp*g")):
                row: dict[str, object] = {
                    "image_id": img.stem,
                    "split": split,
                    "label": label,
                    "flooded": int(label == "flooded"),
                    "image_path": img.relative_to(root).as_posix(),
                }
                for cls in MASK_CLASSES:
                    cand = root / "masks" / cls / split / label / (img.stem + ".png")
                    row[f"mask_{cls}"] = cand.relative_to(root).as_posix() if cand.exists() else None
                rows.append(row)
    if not rows:
        raise FileNotFoundError(f"no images found under {root / 'images'}")
    return pd.DataFrame(rows)


def summarise(df: pd.DataFrame) -> pd.DataFrame:
    """Image and mask counts per split and label; written next to the index as a human-readable check."""
    agg = {"images": ("image_id", "size")}
    for cls in MASK_CLASSES:
        agg[f"with_{cls}_mask"] = (f"mask_{cls}", lambda s: int(s.notna().sum()))
    return df.groupby(["split", "label"]).agg(**agg).reset_index()


def load_index(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"{path} missing; run `floodline index` first")
    return pd.read_parquet(path)


def eval_split(index: pd.DataFrame, split: str = "test", limit: int | None = None) -> pd.DataFrame:
    """Flooded images of one split: the evaluation set for segmentation, depth and assessment."""
    ev = index[(index.split == split) & (index.flooded == 1)].reset_index(drop=True)
    return ev.head(limit) if limit else ev
