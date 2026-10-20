import json

import pandas as pd
import pytest

from floodline import cli

SUBCOMMANDS = [
    "index", "sheets", "depth", "segment", "waterline", "fuse", "satellite", "reach", "exposure", "serve",
    "external", "agree", "sensitivity", "attack", "trust", "validate", "ingest", "release", "overlay", "assess",
]  # fmt: skip


@pytest.mark.parametrize("command", SUBCOMMANDS)
def test_every_subcommand_has_help(command, capsys):
    with pytest.raises(SystemExit) as exit_:
        cli.build_parser().parse_args([command, "--help"])
    assert exit_.value.code == 0 and command in capsys.readouterr().out


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--version"])
    assert capsys.readouterr().out.startswith("floodline ")


@pytest.fixture
def city(tmp_path, monkeypatch):
    """A fused-looking layer CSV for a small synthetic town, and a project root to run the CLI in."""
    h3 = pytest.importorskip("h3")
    monkeypatch.setenv("FLOODLINE_ROOT", str(tmp_path))
    centre = h3.latlng_to_cell(50.5, 7.1, 9)
    wet = set(h3.grid_disk(centre, 1))
    rows = [
        {
            "h3": c,
            "satellite_water_fraction": 0.6 if c in wet else 0.0,
            "buildings_in_flood": 15 if c in wet else 0,
            "buildings_total": 20,
            "cut_off": c == centre,
            "reach_dry_min": 3.0,
            "delay_min": 0.0,
        }
        for c in h3.grid_disk(centre, 3)
    ]
    layer = tmp_path / "layer.csv"
    pd.DataFrame(rows).to_csv(layer, index=False)
    return tmp_path, layer


def test_fuse_sensitivity_and_attack_through_main(city):
    pytest.importorskip("geopandas")
    root, layer = city
    assert cli.main(["fuse", str(layer), "--out", "hexes_town"]) == 0
    hexes = root / "results" / "hexes_town.csv"
    ranked = pd.read_csv(hexes)
    assert ranked.priority.iloc[0] > 0 and bool(ranked.cut_off.iloc[0])
    assert (root / "results" / "hexes_town.parquet").exists()

    assert cli.main(["sensitivity", str(hexes)]) == 0
    summary = json.loads((root / "results" / "sensitivity_run.json").read_text("utf-8"))
    assert summary["metrics"]["variants"] == 131

    assert cli.main(["attack", str(hexes), "--draws", "2"]) == 0
    attack = json.loads((root / "results" / "attack_town.json").read_text("utf-8"))["metrics"]
    assert attack["lone"]["top10_overlap"]["min"] == 1.0


def test_missing_input_is_an_error_not_a_traceback(city, caplog):
    root, _ = city
    assert cli.main(["fuse", str(root / "nope.csv")]) == 1


def test_trust_without_reports(city):
    root, _ = city
    assert cli.main(["trust"]) == 0
    assert json.loads((root / "results" / "trust_run.json").read_text("utf-8"))["metrics"]["reference_images"] == 0
