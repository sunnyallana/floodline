"""Depth-benchmark contact sheets and the label template.

A stratified sample of flooded images is tiled onto numbered sheets for manual depth-class labelling. Labels go
into ``data/depth_labels.csv`` (one of ``dry, ankle, knee, waist, chest, above_head, unknown`` plus a
reference-object note). The template is never overwritten once it exists: labels are hand work.
"""

from __future__ import annotations

import pandas as pd
from PIL import Image, ImageDraw

from floodline.config import Paths
from floodline.cvfd import load_index
from floodline.log import get_logger

log = get_logger(__name__)

TILE = 360
COLS = 4
HEADER = 28


def stratified_sample(flooded: pd.DataFrame, n: int, seed: int) -> pd.DataFrame:
    """Sample ``n`` rows with split proportions preserved, then shuffle so sheets mix splits."""
    parts = []
    for _, g in flooded.groupby("split"):
        k = min(len(g), round(n * len(g) / len(flooded)))
        parts.append(g.sample(n=k, random_state=seed))
    return pd.concat(parts).sample(frac=1, random_state=seed).reset_index(drop=True)


def run(paths: Paths, n: int = 300, per_sheet: int = 12, seed: int = 0, split: str | None = "test") -> int:
    """Sample from one split by default (the test split, so the benchmark images also have SAM 3 masks)."""
    index = load_index(paths.cvfd_index)
    flooded = index[index.flooded == 1]
    if split:
        flooded = flooded[flooded.split == split]
    sample = stratified_sample(flooded, n, seed)
    paths.depth_sheets.mkdir(parents=True, exist_ok=True)

    rows = []
    for start in range(0, len(sample), per_sheet):
        chunk = sample.iloc[start : start + per_sheet]
        nrows = -(-len(chunk) // COLS)
        sheet = Image.new("RGB", (COLS * TILE, nrows * (TILE + HEADER)), "white")
        draw = ImageDraw.Draw(sheet)
        sheet_name = f"sheet_{start // per_sheet:02d}.png"
        for i, (_, r) in enumerate(chunk.iterrows()):
            im = Image.open(paths.cvfd / r.image_path).convert("RGB").resize((TILE, TILE), Image.BICUBIC)
            x, y = (i % COLS) * TILE, (i // COLS) * (TILE + HEADER)
            sheet.paste(im, (x, y + HEADER))
            draw.rectangle([x, y, x + TILE, y + HEADER - 2], fill="black")
            draw.text((x + 6, y + 6), f"#{start + i:03d}  {r.image_id}", fill="white")
            rows.append(
                {
                    "idx": start + i,
                    "image_id": r.image_id,
                    "split": r.split,
                    "image_path": r.image_path,
                    "sheet": sheet_name,
                    "depth_bin": "",
                    "reference": "",
                    "note": "",
                }
            )
        sheet.save(paths.depth_sheets / sheet_name)

    if paths.depth_labels.exists():
        log.info("label template already exists, not overwriting: %s", paths.depth_labels)
    else:
        pd.DataFrame(rows).to_csv(paths.depth_labels, index=False)
    log.info("%d images on %d sheets -> %s", len(rows), -(-len(rows) // per_sheet), paths.depth_sheets)
    return len(rows)
