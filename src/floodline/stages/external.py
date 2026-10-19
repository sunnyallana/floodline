"""External inference set: the European Flood 2013 dataset (Barz et al., Jena), ground-level and geotagged.

The release ships ``metadata.json`` (one record per Wikimedia Commons file: pageid, title, capture time, optional
camera coordinates, licence, author) and hydrologist relevance lists. Files are named ``<pageid>.jpg``. This stage
builds one table of images that can go on a map: located (tier ``gps``), with the licence and author needed for
attribution, and flagged where hydrologists judged the depth estimable.

Layout expected under ``data/external/eu-flood-2013``::

    meta/metadata.json
    meta/relevance/{depth,flooding,pollution,irrelevant}.txt
    imgs/imgs_large/<pageid>.jpg
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from floodline.config import Paths
from floodline.location import TIER_CONFIDENCE, h3_index
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

RELEVANCE_TASKS: tuple[str, ...] = ("depth", "flooding", "pollution", "irrelevant")


def read_relevance(meta_dir: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for task in RELEVANCE_TASKS:
        f = meta_dir / "relevance" / f"{task}.txt"
        out[task] = (
            {line.strip() for line in f.read_text(encoding="utf-8").splitlines() if line.strip()}
            if f.exists()
            else set()
        )
    return out


def build_table(root: Path) -> pd.DataFrame:
    """One row per metadata record, with location tier, H3 cell, relevance flags and the local image path."""
    meta_dir = root / "meta"
    records: list[dict[str, Any]] = json.loads((meta_dir / "metadata.json").read_text(encoding="utf-8"))
    relevance = read_relevance(meta_dir)
    img_dir = root / "imgs" / "imgs_large"
    rows = []
    for r in records:
        pid = str(r["pageid"])
        coords = r.get("coordinates") or {}
        lat, lon = coords.get("lat"), coords.get("lon")
        located = lat is not None and lon is not None
        img = img_dir / f"{pid}.jpg"
        rows.append(
            {
                "image_id": f"euf_{pid}",
                "pageid": pid,
                "title": r.get("title", "").removeprefix("File:"),
                "image_path": img.relative_to(root).as_posix() if img.exists() else None,
                "lat": lat,
                "lon": lon,
                "location_tier": "gps" if located else "none",
                "location_confidence": TIER_CONFIDENCE["gps" if located else "none"],
                "h3": h3_index(lat, lon) if located else None,
                "captured_at": r.get("capture_time"),
                "license": r.get("license"),
                "artist": r.get("artist"),
                "source_url": r.get("descriptionurl"),
                **{f"rel_{t}": pid in relevance[t] for t in RELEVANCE_TASKS},
            }
        )
    return pd.DataFrame(rows)


def run(paths: Paths, root: Path | None = None) -> pd.DataFrame:
    root = root or paths.data / "external" / "eu-flood-2013"
    manifest = RunManifest("external", params={"dataset": "european-flood-2013", "root": str(root)})
    df = build_table(root)
    out = paths.data / "eu_flood_2013_index.parquet"
    df.to_parquet(out, index=False)
    mappable = df[(df.location_tier == "gps") & df.image_path.notna()]
    summary = {
        "records": len(df),
        "with_image": int(df.image_path.notna().sum()),
        "located": int((df.location_tier == "gps").sum()),
        "mappable": len(mappable),
        "mappable_depth_relevant": int(mappable.rel_depth.sum()),
        "h3_cells": int(mappable.h3.nunique()),
        "licences": df.license.value_counts().head(8).to_dict(),
    }
    mappable.drop(columns=["artist"]).to_csv(paths.results / "eu_flood_2013_mappable.csv", index=False)
    manifest.finish(**summary).write(paths.results / "external_run.json")
    log.info("EU Flood 2013: %s", {k: v for k, v in summary.items() if k != "licences"})
    return df
