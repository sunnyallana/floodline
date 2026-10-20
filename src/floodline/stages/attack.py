"""Simulated bogus reports against a real ranking: does the corroboration rule hold?

Starts from a fused city (the layers only, without any report columns) and injects synthetic field reports,
then re-fuses and compares the ranking with the clean one. Scenarios, each repeated over random draws:

- ``lone``: N reports, each from its own device, chest-deep with 5 people up high, in hexagons the satellite
  maps dry. The design says: nothing in the ranking moves, every one of them lands on the verification list.
- ``colluding``: pairs of devices agreeing on the same claim in M dry hexagons. This is the residual risk named
  in THREAT_MODEL.md; the scenario measures how far it reaches.
- ``wet_exaggeration``: N lone reports in hexagons the satellite already maps wet, claiming 5 people up high.
  Satellite water corroborates the depth, so the people claim counts; this measures that exposure.

Reports pass through ``fuse.aggregate`` and ``fuse.score`` exactly as real ones do (trust ``accepted``: the
attacker is assumed to have a valid team code and photos that pass every trust check).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest
from floodline.stages.fuse import combine, score

log = get_logger(__name__)

REPORT_COLUMNS = (
    "n_reports", "n_devices", "n_with_depth", "depth_claim", "depth_agreeing_devices", "depth_assessed",
    "people_visible", "people_in_water", "people_elevated", "assessment_confidence", "location_confidence",
    "first_report", "last_report", "n_unverified",
)  # fmt: skip
SCORE_COLUMNS = (
    "exposure", "hazard", "access", "priority", "confidence", "corroborated", "needs_verification",
    "verify_score", "photo_satellite_agreement", "buildings_affected", "satellite_water_near",
)  # fmt: skip


def layers_only(hexes: pd.DataFrame) -> pd.DataFrame:
    return hexes.drop(columns=[c for c in (*REPORT_COLUMNS, *SCORE_COLUMNS) if c in hexes.columns])


def fake_reports(cells: list[str], devices_per_cell: int, seed: int) -> pd.DataFrame:
    import h3

    rows = []
    for i, cell in enumerate(cells):
        lat, lon = h3.cell_to_latlng(cell)
        for d in range(devices_per_cell):
            rows.append(
                {
                    "lat": lat, "lon": lon, "device_id": f"attacker-{seed}-{i}-{d}", "trust": "accepted",
                    "depth_class": "chest", "people_visible": 5, "people_in_water": 0,
                    "people_on_roofs_or_elevated": 5, "location_confidence": 0.95, "confidence": 0.5,
                }
            )  # fmt: skip
    return pd.DataFrame(rows)


def _ranked(base: pd.DataFrame, reports: pd.DataFrame | None) -> pd.DataFrame:
    return score(combine([base], reports)).set_index("h3")


def scenario(base: pd.DataFrame, clean: pd.DataFrame, cells: list[str], devices: int, seed: int, k: int = 10) -> dict:
    attacked = _ranked(base, fake_reports(cells, devices, seed))
    top_clean = set(clean.priority.sort_values(ascending=False).head(k).index)
    top_attacked = set(attacked.priority.sort_values(ascending=False).head(k).index)
    targets = attacked.loc[cells]
    return {
        f"top{k}_overlap": len(top_clean & top_attacked) / k,
        "targets_in_top": len(set(cells) & top_attacked),
        "targets_raised": int((targets.priority > clean.loc[cells].priority + 1e-9).sum()),
        "targets_flagged": int(targets.needs_verification.sum()),
        "targets": len(cells),
    }


def run(paths: Paths, hexes_csv: Path, draws: int = 20, lone: int = 50, pairs: int = 10, seed: int = 0) -> dict:
    hexes = pd.read_csv(hexes_csv)
    base = layers_only(hexes)
    clean = _ranked(base, None)
    rng = np.random.default_rng(seed)
    mapped = base[base.satellite_water_fraction.notna()]
    near = clean.satellite_water_near.reindex(mapped.h3).fillna(False).to_numpy()
    dry = mapped.h3[(~near) & (mapped.satellite_water_fraction.fillna(0).to_numpy() == 0)].tolist()
    wet = mapped.h3[mapped.satellite_water_fraction.fillna(0).to_numpy() > 0].tolist()
    results: dict[str, list[dict]] = {"lone": [], "colluding": [], "wet_exaggeration": []}
    for draw in range(draws):
        results["lone"].append(
            scenario(base, clean, list(rng.choice(dry, size=min(lone, len(dry)), replace=False)), 1, draw)
        )
        results["colluding"].append(
            scenario(base, clean, list(rng.choice(dry, size=min(pairs, len(dry)), replace=False)), 2, draw)
        )
        results["wet_exaggeration"].append(
            scenario(base, clean, list(rng.choice(wet, size=min(lone, len(wet)), replace=False)), 1, draw)
        )
    summary: dict[str, Any] = {"dry_hexes": len(dry), "wet_hexes": len(wet), "draws": draws}
    for name, rows in results.items():
        df = pd.DataFrame(rows)
        summary[name] = {
            c: {"mean": round(float(df[c].mean()), 3), "max": float(df[c].max()), "min": float(df[c].min())} for c in df
        }
    out = paths.results / f"attack_{hexes_csv.stem.removeprefix('hexes_')}.json"
    RunManifest("attack", params={"hexes": str(hexes_csv), "draws": draws, "lone": lone, "pairs": pairs}).finish(
        **summary
    ).write(out)
    log.info("attack: %s", summary)
    return summary
