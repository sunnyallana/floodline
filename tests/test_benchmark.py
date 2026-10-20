import numpy as np
import pandas as pd
import pytest

h3 = pytest.importorskip("h3")

from floodline import population  # noqa: E402
from floodline.stages import benchmark  # noqa: E402


def test_ghsl_tile_index():
    # Values checked against tiles that exist on the JRC server (R3_C19, R4_C19 for western Germany).
    pytest.importorskip("pyproj")
    assert population.tile_of(536_198, 5_929_904) == "R4_C19"
    assert population.tiles_for((6.9, 50.4, 7.2, 50.6)) == ["R4_C19"]
    assert population.tile_of(-18_041_000 + 1, 9_000_000 - 1) == "R1_C1"


def _town(seed: int, name: str, n: int = 400) -> pd.DataFrame:
    """Hexagons in one town where damage follows ``signal``; ``noise`` is unrelated to it."""
    rng = np.random.default_rng(seed)
    centre = h3.latlng_to_cell(50.0 + seed, 7.0, 9)
    cells = list(h3.grid_disk(centre, 12))[:n]
    signal = rng.random(len(cells))
    severe = np.where(signal > 0.9, rng.integers(1, 20, len(cells)), 0)
    return pd.DataFrame(
        {
            "h3": cells,
            "priority": signal,
            "people_in_flood": rng.random(len(cells)),
            "buildings_in_flood": signal * 0.5 + rng.random(len(cells)) * 0.5,
            "satellite_water_fraction": rng.random(len(cells)),
            "severe": severe,
            "town": name,
            "event": "TEST",
        }
    )


def test_macro_capture_rewards_the_informative_score():
    df = pd.concat([_town(1, "a"), _town(2, "b")], ignore_index=True)
    assert benchmark.macro_capture(df, "priority") > 0.95  # damage sits in the top ~10 % by construction
    assert benchmark.macro_capture(df, "people_in_flood") < 0.4  # unrelated score: near the 10 % random level


def test_block_bootstrap_gives_ordered_intervals_and_a_clear_win():
    df = pd.concat([_town(1, "a"), _town(2, "b")], ignore_index=True)
    out = benchmark.bootstrap(df, n=50, seed=1)
    lo, hi = out["priority"]["ci95"]
    assert lo <= out["priority"]["top10"] <= hi
    win = out["priority_minus_people_in_flood"]
    assert win["ci95"][0] > 0 and win["share_of_draws_priority_better"] == 1.0
    assert out["priority_minus_buildings_in_flood"]["diff"] > 0


def test_blocks_are_resolution_6_parents():
    cell = h3.latlng_to_cell(50.0, 7.0, 9)
    assert benchmark.blocks(pd.Series([cell])).iloc[0] == h3.cell_to_parent(cell, 6)
