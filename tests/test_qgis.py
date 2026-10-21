import importlib.util
import json
import os
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

gpd = pytest.importorskip("geopandas")
pyogrio = pytest.importorskip("pyogrio")

from floodline.config import Paths  # noqa: E402
from floodline.stages import qgis  # noqa: E402
from floodline.stages.fuse import Weights, score  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _rerank_module():
    spec = importlib.util.spec_from_file_location("floodline_rerank", ROOT / "qgis" / "floodline_rerank.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _collection(features):
    return json.dumps({"type": "FeatureCollection", "features": features})


def _square(x, y, d=0.001):
    return {"type": "Polygon", "coordinates": [[[x, y], [x + d, y], [x + d, y + d], [x, y + d], [x, y]]]}


def _city(directory: Path) -> Path:
    directory.mkdir(parents=True)
    hexes = [
        {"type": "Feature", "geometry": _square(0, 0), "properties": {"h3": "a", "priority": 0.2, "cut_off": False}},
        {"type": "Feature", "geometry": _square(1, 0), "properties": {"h3": "b", "priority": 0.9, "cut_off": True}},
        {"type": "Feature", "geometry": _square(2, 0), "properties": {"h3": "c", "priority": 0.5, "cut_off": False}},
    ]
    (directory / "hexes.geojson").write_text(_collection(hexes), encoding="utf-8")
    (directory / "flood.geojson").write_text(
        _collection([{"type": "Feature", "geometry": _square(0, 0, 3), "properties": {"kind": "flood"}}]),
        encoding="utf-8",
    )
    (directory / "roads.geojson").write_text(_collection([]), encoding="utf-8")  # published but empty
    (directory / "staging.geojson").write_text(
        _collection(
            [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0.5, 0.5]},
                    "properties": {"kind": "hospital"},
                }
            ]
        ),
        encoding="utf-8",
    )
    return directory


def test_export_city_ranks_hexes_and_skips_empty_layers(tmp_path):
    gpkg = tmp_path / "out" / "city.gpkg"
    counts = qgis.export_city(_city(tmp_path / "city"), gpkg)

    assert counts == {"flood_extent": 1, "priority_hexes": 3, "staging_points": 1}
    assert {name for name, _ in pyogrio.list_layers(gpkg)} == set(counts)
    hexes = gpd.read_file(gpkg, layer="priority_hexes")
    assert list(hexes.h3) == ["b", "c", "a"]
    assert list(hexes["rank"]) == [1, 2, 3]
    assert bool(hexes.cut_off.iloc[0]) is True


def test_export_city_replaces_an_earlier_file(tmp_path):
    city, gpkg = _city(tmp_path / "city"), tmp_path / "city.gpkg"
    qgis.export_city(city, gpkg)
    qgis.export_city(city, gpkg)
    assert len(gpd.read_file(gpkg, layer="priority_hexes")) == 3


def test_export_city_needs_a_published_city(tmp_path):
    with pytest.raises(FileNotFoundError, match="floodline serve"):
        qgis.export_city(tmp_path / "missing", tmp_path / "x.gpkg")


@pytest.mark.parametrize(
    "weights", [Weights(), Weights(exposure=0.2, hazard=0.3, access=0.5), Weights(exposure=0.6, hazard=0.6, access=0.6)]
)
def test_rerank_script_matches_fuse_score(weights):
    rng = np.random.default_rng(7)
    n = 200
    hexes = pd.DataFrame(
        {
            "h3": [f"h{i}" for i in range(n)],
            # A share of hexes hold a sliver of water that rounds away in the published hazard (Larkana has two).
            "satellite_water_fraction": np.select(
                [rng.random(n) < 0.3, rng.random(n) < 0.15], [0.0, rng.random(n) * 4e-4], rng.random(n)
            ),
            "buildings_in_flood": rng.integers(0, 60, n),
            "buildings_total": rng.integers(60, 120, n),
            "cut_off": rng.random(n) < 0.2,
            "delay_min": rng.random(n) * 50,
            "reach_dry_min": rng.random(n) * 10,
        }
    )
    scored = score(hexes, weights)
    rerank = _rerank_module()
    # The script sees what serve.py publishes: components at 3 decimals, the water fraction at 4, the priority.
    mine = [
        rerank.fused_priority(
            round(r.exposure, 3),
            round(r.hazard, 3),
            round(r.access, 3),
            r.cut_off,
            weights.exposure,
            weights.hazard,
            weights.access,
            water=round(r.satellite_water_fraction, 4),
            published=r.priority,
        )
        for r in scored.itertuples()
    ]
    assert np.allclose(mine, scored.priority, atol=1.5e-3)  # one rounding step of the components


def test_rerank_gate_nulls_and_ranks():
    rerank = _rerank_module()
    assert rerank.fused_priority(1.0, 0.0, 1.0, False, 0.45, 0.3, 0.25) == 0.0  # dry and reachable: no priority
    assert rerank.fused_priority(1.0, None, 1.0, True, 0.45, 0.3, 0.25) == 0.7  # cut off counts even when dry
    assert rerank.fused_priority(None, 0.5, None, None, 1.0, 1.0, 1.0) == 0.5
    # hazard rounded to 0.000 but water mapped: still wet, so access counts (the two Larkana hexagons)
    assert rerank.fused_priority(0.0, 0.0, 0.653, False, 0.45, 0.3, 0.25, water=0.0005) == 0.163
    assert rerank.fused_priority(0.0, 0.0, 0.653, False, 0.45, 0.3, 0.25, published=0.163) == 0.163
    assert rerank.ranks([0.2, 0.9, 0.2, 0.5]) == [3, 1, 4, 2]


def test_find_qgis_python(tmp_path, monkeypatch):
    launcher = tmp_path / "python-qgis.bat"
    launcher.write_text("")
    monkeypatch.setenv(qgis.QGIS_PYTHON_ENV, str(launcher))
    assert qgis.find_qgis_python() == launcher
    assert qgis.find_qgis_python(str(launcher)) == launcher
    with pytest.raises(FileNotFoundError):
        qgis.find_qgis_python(str(tmp_path / "absent.bat"))
    monkeypatch.delenv(qgis.QGIS_PYTHON_ENV)
    monkeypatch.setattr(qgis.glob, "glob", lambda pattern: [])
    assert qgis.find_qgis_python() is None


def _paths_with_city(tmp_path) -> Paths:
    _city(tmp_path / "docs" / "map" / "data" / "town")
    (tmp_path / "docs" / "map" / "cities.json").write_text(json.dumps([{"key": "town", "title": "Town, 2024"}]))
    return Paths(root=tmp_path)


def test_run_without_qgis_still_writes_the_geopackage(tmp_path, monkeypatch):
    monkeypatch.delenv(qgis.QGIS_PYTHON_ENV, raising=False)
    monkeypatch.setattr(qgis.glob, "glob", lambda pattern: [])
    out = qgis.run(_paths_with_city(tmp_path), "town")
    assert out["project"] is None
    assert Path(out["gpkg"]) == tmp_path / "docs" / "qgis" / "town" / "floodline_town.gpkg"
    assert Path(out["gpkg"]).exists()


def test_run_hands_the_geopackage_to_qgis(tmp_path, monkeypatch):
    launcher = tmp_path / "python-qgis.bat"
    launcher.write_text("")
    calls = []
    monkeypatch.setattr(qgis.subprocess, "run", lambda cmd, check: calls.append(cmd))
    out = qgis.run(_paths_with_city(tmp_path), "town", qgis_python=str(launcher))
    assert calls == [
        [str(launcher), str(tmp_path / qgis.PROJECT_SCRIPT), out["gpkg"], "--title", "Floodline: Town, 2024"]
    ]
    assert out["project"].endswith("floodline_town.qgz")


@pytest.mark.skipif(not os.environ.get(qgis.QGIS_PYTHON_ENV), reason="needs QGIS: set FLOODLINE_QGIS_PYTHON")
@pytest.mark.parametrize("key", ["prague", "dresden", "larkana"])
def test_published_projects_open_and_rerank_in_qgis(key):
    qgz = ROOT / "docs" / "qgis" / key / f"floodline_{key}.qgz"
    done = subprocess.run(
        [os.environ[qgis.QGIS_PYTHON_ENV], str(ROOT / "qgis" / "verify.py"), str(qgz)], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.count("ok  ") == 3
