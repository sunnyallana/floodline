"""Paths and defaults for every stage, resolved once.

Nothing here imports torch or transformers; the base package must stay importable on a machine without them.
Override the project root with ``FLOODLINE_ROOT`` (the ``v2`` directory) when running outside the repo.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    """Filesystem layout. ``root`` is the ``v2`` directory; raw CVFD lives one level up in ``../data/cvfd``."""

    root: Path = field(default_factory=lambda: Path(os.environ.get("FLOODLINE_ROOT", _DEFAULT_ROOT)))
    namespace: str = ""  # results for a non-default dataset go to results/<namespace>/

    @property
    def cvfd(self) -> Path:
        return Path(os.environ.get("FLOODLINE_CVFD", self.root / "data" / "cvfd"))

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def results(self) -> Path:
        return self.root / "results" / self.namespace if self.namespace else self.root / "results"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    # Derived artefacts, named in one place so stages and tests agree on them.
    @property
    def cvfd_index(self) -> Path:
        return self.data / "cvfd_index.parquet"

    @property
    def depth_labels(self) -> Path:
        return self.data / "depth_labels.csv"

    @property
    def depth_sheets(self) -> Path:
        return self.data / "depth_sheets"

    @property
    def depth_maps(self) -> Path:
        return self.results / "depth_maps"

    @property
    def sam3_masks(self) -> Path:
        return self.results / "sam3_masks"

    @property
    def sam3_scores(self) -> Path:
        return self.results / "sam3_scores.csv"

    @property
    def vlm_assessments(self) -> Path:
        return self.results / "vlm_assessments.jsonl"

    def ensure(self) -> Paths:
        for p in (self.data, self.results):
            p.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class ModelIds:
    """Hugging Face identifiers and local GGUF names. Changing a model means changing it here, once."""

    sam3: str = "facebook/sam3"
    depth: str = "depth-anything/Depth-Anything-V2-Metric-Outdoor-Large-hf"
    vlm_gguf: str = "Qwen2.5-VL-7B-Instruct-Q8_0.gguf"
    vlm_mmproj: str = "mmproj-Qwen2.5-VL-7B-Instruct-f16.gguf"


SAM3_PROMPTS: tuple[str, ...] = ("water", "person", "car", "motorbike", "door", "window", "building")
"""Concept prompts. Each either measures depth (an object of known height) or counts impact."""

SAM3_GROUND_TRUTH: dict[str, str] = {
    "water": "mask_flood",
    "person": "mask_humans",
    "building": "mask_buildings",
}
"""Prompts that have a CVFD mask to be scored against, and the index column holding that mask's path."""


def default_threads(reserve: int = 2) -> int:
    """Leave a couple of cores free so the machine stays usable during long CPU runs."""
    return max(1, (os.cpu_count() or 4) - reserve)
