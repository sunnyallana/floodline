import json

import numpy as np
import pandas as pd
import pytest

h3 = pytest.importorskip("h3")

from floodline.config import Paths  # noqa: E402
from floodline.stages import benchmark, site  # noqa: E402


def _town(root, folder: str, event: str, aoi: str, seed: int, water_scale: float = 1.0) -> None:
    """A ranked town on disk: damage follows exposure inside the water; the satellite fraction is noisy."""
    rng = np.random.default_rng(seed)
    cells = list(h3.grid_disk(h3.latlng_to_cell(40.0 + seed, 10.0, 9), 10))[:300]
    exposure = rng.random(len(cells))
    hazard = (rng.random(len(cells)) > 0.4) * rng.random(len(cells))
    severe = np.where((exposure > 0.8) & (hazard > 0), rng.integers(1, 9, len(cells)), 0)
    d = root / "results" / folder / event
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"h3": cells, "severe": severe, "affected": severe * 2}).to_csv(d / f"ranking_{aoi}.csv", index=False)
    pd.DataFrame(
        {
            "h3": cells,
            "exposure": exposure,
            "hazard": hazard,
            "access": rng.random(len(cells)),
            "cut_off": rng.random(len(cells)) > 0.95,
            "buildings_affected": (exposure * 20).round(),
            "people_total": (exposure * 80).round(),
            "people_in_flood": (exposure * hazard * 80).round(),
            "satellite_water_fraction": np.clip(hazard * water_scale, 0, 1),
            "buildings_in_flood": (exposure * hazard * 20).round(),
            "priority": 0.45 * exposure + 0.30 * hazard + 0.25 * rng.random(len(cells)),
        }
    ).to_csv(d / f"hexes_{aoi}.csv", index=False)
    run = {
        "towns": {aoi: {"town": f"Town {aoi}", "ranked_hexes": len(cells), "severe_in_mapped_hexes": int(severe.sum())}}
    }
    (d / "event_run.json").write_text(json.dumps({"metrics": run}), encoding="utf-8")


@pytest.fixture()
def tree(tmp_path):
    for folder, scale in (("benchmark", 1.0), ("benchmark_osm", 0.5)):
        _town(tmp_path, folder, "EMSR517", "aoi07", 1, scale)
        _town(tmp_path, folder, "EMSR631", "aoi01", 2, scale)
    return Paths(root=tmp_path).ensure()


def test_summary_reports_ablations_per_event_and_data_ablations(tree):
    out = benchmark.summarise(tree, n=5)
    assert out["towns"] == 2 and set(out["events"]) == {"EMSR517", "EMSR631"}
    per = out["ablation_per_event"]["EHA"]
    assert set(per["events"]) == {"EMSR517", "EMSR631"} and per["min"] <= per["mean"]
    assert per["worst_event"] in per["events"]
    assert set(out["leave_one_event_out"]) >= {"EMSR517", "EMSR631", "mean_over_events"}
    osm = out["data_ablations"]["osm_buildings_only"]
    assert len(osm["towns"]) == 2 and set(osm["per_event_EHA"]["ablated"]["events"]) == {"EMSR517", "EMSR631"}
    assert osm["water_share_median"]["ablated"] == pytest.approx(osm["water_share_median"]["default"] / 2, rel=0.05)
    assert "sentinel1_change" not in out["data_ablations"]  # never run here, so never reported
    assert (tree.results / "benchmark_summary.json").exists() and (tree.results / "benchmark_towns.csv").exists()


def test_site_renders_every_number_from_result_files(tree):
    benchmark.summarise(tree, n=5)
    page = site.run(tree, out_dir=tree.root / "docs")
    data = json.loads((tree.root / "docs" / "site.json").read_text(encoding="utf-8"))
    assert page.exists() and "<html" in page.read_text(encoding="utf-8").lower()
    assert [e["code"] for e in data["events"]] == ["EMSR517", "EMSR631"]
    assert data["benchmark"]["towns"] == 2 and "EHA" in data["benchmark"]["per_event_ablation"]
    assert data["perception"]["vlm"] is None  # missing results show as pending, not as numbers
    assert data["links"]["questions"].endswith("REVIEWER_QUESTIONS.md")
