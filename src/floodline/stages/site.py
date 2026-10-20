"""The project page: ``docs/index.html`` plus ``docs/site.json``, every number read from a result file.

The page never hard-codes a result. ``floodline site`` gathers them from ``results/`` (benchmark summary, road
rule, trust and attack runs, Sentinel-1 checks, perception runs) into one JSON file the page renders, so a rerun
of any stage followed by ``floodline site`` updates the page, and a missing result shows as "pending".
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from floodline.config import Paths
from floodline.log import get_logger

log = get_logger(__name__)

EVENTS: dict[str, dict[str, str]] = {
    "EMSR517": {"name": "Western Germany", "country": "Germany", "when": "July 2021", "kind": "flash flood"},
    "EMSR631": {"name": "Sanghar, Sindh", "country": "Pakistan", "when": "September 2022", "kind": "riverine flood"},
    "EMSR504": {"name": "New South Wales", "country": "Australia", "when": "March 2021", "kind": "riverine flood"},
    "EMSR559": {"name": "Moramanga", "country": "Madagascar", "when": "January 2022", "kind": "flood"},
    "EMSR634": {"name": "Marche", "country": "Italy", "when": "September 2022", "kind": "flash flood"},
    "EMSR649": {"name": "Pachino, Sicily", "country": "Italy", "when": "February 2023", "kind": "flash flood"},
}


def _read(path: Path) -> Any:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("metrics", data) if isinstance(data, dict) else data


def gather(paths: Paths) -> dict[str, Any]:
    r = paths.results
    bench = _read(r / "benchmark_summary.json") or {}
    events = []
    for code, meta in EVENTS.items():
        run = _read(r / "benchmark" / code / "event_run.json")
        if not run:
            continue
        towns = [t for t in run["towns"].values() if t.get("ranked_hexes")]
        events.append(
            {
                "code": code,
                **meta,
                "towns": [t["town"] for t in towns],
                "severe": int(sum(t.get("severe_in_mapped_hexes", 0) for t in towns)),
                "hexes": int(sum(t["ranked_hexes"] for t in towns)),
            }
        )
    roads = [row for row in bench.get("roads", []) if row.get("target") == "affected"]
    return {
        "events": events,
        "benchmark": {
            "towns": bench.get("towns"),
            "hexes": bench.get("hexes"),
            "severe": bench.get("severe_buildings"),
            "ablation": bench.get("ablation", {}),
            "loeo": bench.get("leave_one_event_out", {}),
            "data_ablations": bench.get("data_ablations", {}),
            "per_event": bench.get("per_event_macro_top10", {}),
            "per_event_ablation": bench.get("ablation_per_event", {}),
        },
        "roads": roads,
        "attack": {"strict": _read(r / "attack_prague.json"), "permissive": _read(r / "attack_prague_permissive.json")},
        "trust": _read(r / "trust_eval.json"),
        "sentinel1_larkana": _read(r / "sentinel1_larkana_run.json"),
        "agreement": _read(r / "agreement_euflood_run.json"),
        "perception": {
            "sam3": _read(r / "sam3_run.json"),
            "waterline": _read(r / "waterline_run.json"),
            "vlm": _read(r / "vlm_benchmark.json"),
        },
        "links": {
            "map": "map/",
            "field": "field/",
            "review": "review/",
            "code": "https://github.com/sunnyallana/floodline",
            "questions": "https://github.com/sunnyallana/floodline/blob/main/REVIEWER_QUESTIONS.md",
            "paper": "https://github.com/sunnyallana/floodline/blob/main/paper/floodline.pdf",
            "dataset": "https://huggingface.co/datasets/sunnyallana/flood-depth-benchmark",
        },
    }


def run(paths: Paths, out_dir: Path | None = None) -> Path:
    out_dir = out_dir or paths.root / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    data = gather(paths)
    (out_dir / "site.json").write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    page = out_dir / "index.html"
    page.write_text(resources.files("floodline.web").joinpath("site.html").read_text("utf-8"), encoding="utf-8")
    log.info("site: %s (%d events)", page, len(data["events"]))
    return page
