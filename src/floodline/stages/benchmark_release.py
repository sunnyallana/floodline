"""Package the multi-event benchmark as a dataset others can rank against.

One row per H3 resolution-9 hexagon of every ranked town: the independent damage label (EMS grading) next to
each input layer and each score, so a new prioritisation method can be dropped in and compared on the same
hexagons with ``floodline.stages.benchmark.macro_capture``. No photos, no personal data: hexagon aggregates only.
"""

from __future__ import annotations

from pathlib import Path

from floodline.config import Paths
from floodline.log import get_logger
from floodline.stages.benchmark import load_rankings

log = get_logger(__name__)

COLUMNS: tuple[str, ...] = (
    "event", "town", "h3", "severe", "affected",
    "satellite_water_fraction", "buildings_in_flood", "buildings_affected", "people_in_flood", "people_total",
    "cut_off", "hazard", "exposure", "access", "priority", "priority_people",
)  # fmt: skip

CARD = """---
license: odbl
pretty_name: Floodline flood-response prioritisation benchmark
task_categories:
- tabular-classification
tags:
- flood
- disaster-response
- copernicus-ems
- damage-assessment
- h3
- geospatial
size_categories:
- 10K<n<100K
---

# Floodline flood-response prioritisation benchmark

Which neighbourhoods should a flood response reach first? This set lets a ranking method be scored against
independent damage evidence on the same hexagons: {towns} graded towns from {events} Copernicus EMS activations
({event_list}), {hexes:,} H3 resolution-9 hexagons, {severe:,} buildings graded damaged or destroyed by EMS image
analysts.

## Columns

| Column | Meaning |
|---|---|
| `event`, `town` | EMS activation code and graded area |
| `h3` | H3 cell, resolution 9 (~0.1 km2) |
| `severe` | buildings graded "Damaged" or "Destroyed" by EMS (the target) |
| `affected` | the same plus "Possibly damaged" (close to mechanical with the flood extent) |
| `satellite_water_fraction` | share of the hexagon inside the EMS flood polygons |
| `buildings_in_flood`, `buildings_affected` | footprints in the flood (max of OSM and Microsoft ML); + cut off |
| `people_in_flood`, `people_total` | GHSL residents (GHS-POP R2023A, 2020) inside the flood and in the hexagon |
| `cut_off` | the hexagon has roads but none reachable once roads >= 5 m under water are removed |
| `hazard`, `exposure`, `access` | Floodline's score components |
| `priority`, `priority_people` | Floodline's priority with building- and people-based exposure |

## Scoring

The headline metric is macro top-10 % capture: for each town, the share of its `severe` buildings that fall in
its top 10 % of hexagons by a score, averaged over towns. Ties count against the score.

## Sources and licences

Copernicus Emergency Management Service rapid-mapping products (c) European Union, free and open with
attribution. OpenStreetMap (c) contributors and Microsoft Global ML Building Footprints, both ODbL, which this
derived database therefore carries. GHSL population (c) European Commission JRC, CC BY 4.0.
Code and protocol: https://github.com/sunnyallana/floodline (`floodline benchmark`).
"""


def run(paths: Paths, out_dir: Path | None = None) -> Path:
    df = load_rankings(paths)
    if df.empty:
        raise FileNotFoundError("no benchmark rankings yet; run `floodline benchmark <event>` first")
    out_dir = out_dir or paths.data / "bundle" / "floodline-benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    table = df[[c for c in COLUMNS if c in df]]
    table.to_parquet(out_dir / "hexagons.parquet", index=False)
    table.to_csv(out_dir / "hexagons.csv", index=False)
    events = sorted(df.event.unique())
    (out_dir / "README.md").write_text(
        CARD.format(
            towns=df.town.nunique(),
            events=len(events),
            event_list=", ".join(events),
            hexes=len(df),
            severe=int(df.severe.sum()),
        ),
        encoding="utf-8",
    )
    log.info("benchmark bundle: %s (%d hexagons, %d towns)", out_dir, len(df), df.town.nunique())
    return out_dir
