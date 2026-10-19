"""Image sets the perception stages can run on, behind one interface.

Each set yields a frame with at least ``image_id`` and ``image_path`` (relative to the set's root). CVFD also
carries mask columns for scoring; the geotagged European Flood 2013 set carries ``lat``, ``lon``, ``h3`` and
``captured_at`` instead. Stages read images as ``dataset.root / row.image_path`` and never assume a layout.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from floodline.config import Paths
from floodline.cvfd import eval_split, load_index

DATASETS: tuple[str, ...] = ("cvfd", "euflood")


@dataclass(frozen=True)
class Dataset:
    name: str
    root: Path
    frame: pd.DataFrame

    @property
    def has_masks(self) -> bool:
        return "mask_flood" in self.frame.columns

    def image(self, row) -> Path:
        return self.root / row.image_path


def load(paths: Paths, name: str = "cvfd", limit: int | None = None, depth_relevant_only: bool = False) -> Dataset:
    if name == "cvfd":
        return Dataset(name, paths.cvfd, eval_split(load_index(paths.cvfd_index), limit=limit))
    if name == "euflood":
        index = paths.data / "eu_flood_2013_index.parquet"
        if not index.exists():
            raise FileNotFoundError(f"{index} missing; run `floodline external` first")
        df = pd.read_parquet(index)
        df = df[(df.location_tier == "gps") & df.image_path.notna()]
        if depth_relevant_only:
            df = df[df.rel_depth]
        df = df.reset_index(drop=True)
        return Dataset(name, paths.data / "external" / "eu-flood-2013", df.head(limit) if limit else df)
    raise ValueError(f"unknown dataset {name!r}; expected one of {DATASETS}")
