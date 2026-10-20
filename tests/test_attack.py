import pandas as pd
import pytest

h3 = pytest.importorskip("h3")

from floodline.stages import attack  # noqa: E402
from floodline.stages.fuse import combine, score  # noqa: E402


@pytest.fixture
def city():
    """A small mapped town: a flooded street of houses next to a dry district."""
    centre = h3.latlng_to_cell(50.5, 7.1, 9)
    cells = list(h3.grid_disk(centre, 4))
    wet = set(h3.grid_disk(centre, 1))
    rows = [
        {
            "h3": c,
            "satellite_water_fraction": 0.6 if c in wet else 0.0,
            "buildings_in_flood": 15 if c in wet else 0,
            "buildings_total": 20,
            "cut_off": False,
            "reach_dry_min": 3.0,
            "delay_min": 0.0,
        }
        for c in cells
    ]
    return pd.DataFrame(rows), wet


def _clean(base):
    return score(combine([base], None)).set_index("h3")


def test_lone_and_colluding_fakes_in_dry_hexagons_change_nothing(city):
    base, wet = city
    clean = _clean(base)
    dry = [c for c in base.h3 if c not in wet and not clean.loc[c, "satellite_water_near"]][:5]
    for devices in (1, 2):
        result = attack.scenario(base, clean, dry, devices, seed=0, k=5)
        assert result["top5_overlap"] == 1.0 and result["targets_raised"] == 0
        assert result["targets_flagged"] == len(dry)


def test_people_claims_inside_water_need_a_second_source(city):
    base, wet = city
    clean = _clean(base)
    attacked = score(combine([base], attack.fake_reports(sorted(wet)[:1], 1, seed=0))).set_index("h3")
    target = sorted(wet)[0]
    assert attacked.loc[target, "exposure"] == pytest.approx(clean.loc[target, "exposure"])  # people not counted
    assert bool(attacked.loc[target, "needs_verification"])


def test_layers_only_strips_report_and_score_columns(city):
    base, _ = city
    fused = score(combine([base], attack.fake_reports([base.h3.iloc[0]], 1, seed=0)))
    stripped = attack.layers_only(fused)
    assert not {"priority", "n_reports", "depth_claim", "satellite_water_near"} & set(stripped.columns)
