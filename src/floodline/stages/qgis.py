"""Stage 7: a city as a QGIS project, for a coordinator who works in desktop GIS rather than a browser.

``floodline qgis <city>`` reads a city that ``floodline serve`` has published (``docs/map/data/<city>/``) and writes

    docs/qgis/<city>/floodline_<city>.gpkg  one GeoPackage: ranked hexes, flood extent, mapped area, closed
                                            roads, staging points, photos
    docs/qgis/<city>/floodline_<city>.qgz   the project: those layers styled like the web map, over OpenStreetMap

The GeoPackage is written here with GeoPandas alone. Styling and the project need QGIS itself, so the second step
runs ``qgis/make_project.py`` under QGIS's own Python when it can find one (``--qgis-python``, the
``FLOODLINE_QGIS_PYTHON`` environment variable, or a standard Windows install); otherwise it prints the command.
The styles are also saved inside the GeoPackage, so dragging the file alone into QGIS shows the same map.

Re-ranking with other weights happens inside QGIS: ``qgis/floodline_rerank.py`` is a Processing tool that applies
the formula of ``fuse.score`` to the published exposure, hazard and access columns.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger

log = get_logger(__name__)

# Published GeoJSON name -> GeoPackage layer name, drawn bottom to top in the project.
LAYERS: tuple[tuple[str, str], ...] = (
    ("flood", "flood_extent"),
    ("aoi", "mapped_area"),
    ("hexes", "priority_hexes"),
    ("roads", "closed_roads"),
    ("staging", "staging_points"),
    ("photos", "photos"),
)
QGIS_PYTHON_ENV = "FLOODLINE_QGIS_PYTHON"
WINDOWS_QGIS_GLOB = "C:/Program Files/QGIS */bin/python-qgis*.bat"
PROJECT_SCRIPT = "qgis/make_project.py"


def ranked(hexes: pd.DataFrame) -> pd.DataFrame:
    """Highest priority first, with ``rank`` 1..n; ties keep the published order."""
    out = hexes.sort_values("priority", ascending=False, kind="stable").reset_index(drop=True)
    out.insert(0, "rank", range(1, len(out) + 1))
    return out


def export_city(city_dir: Path, gpkg: Path) -> dict[str, int]:
    """Write every non-empty published layer of one city into ``gpkg``; returns features per layer."""
    import geopandas as gpd

    if not (city_dir / "hexes.geojson").exists():
        raise FileNotFoundError(f"{city_dir} has no hexes.geojson; run `floodline serve` for this city first")
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    gpkg.unlink(missing_ok=True)  # a rerun replaces the file rather than appending duplicate layers
    counts: dict[str, int] = {}
    for source, layer in LAYERS:
        path = city_dir / f"{source}.geojson"
        if not path.exists() or not json.loads(path.read_text(encoding="utf-8")).get("features"):
            continue
        gdf = gpd.read_file(path)
        if source == "hexes":
            gdf = gpd.GeoDataFrame(ranked(gdf), geometry="geometry", crs=gdf.crs)
        gdf.to_file(gpkg, layer=layer, driver="GPKG")
        counts[layer] = len(gdf)
    return counts


def find_qgis_python(explicit: str | None = None) -> Path | None:
    """QGIS's Python launcher: the argument, then the environment variable, then a standard Windows install."""
    for candidate in (explicit, os.environ.get(QGIS_PYTHON_ENV)):
        if candidate:
            path = Path(candidate)
            if not path.exists():
                raise FileNotFoundError(f"QGIS Python not found at {path}")
            return path
    installs = sorted(glob.glob(WINDOWS_QGIS_GLOB))
    return Path(installs[-1]) if installs else None


def project_command(qgis_python: Path, script: Path, gpkg: Path, title: str) -> list[str]:
    return [str(qgis_python), str(script), str(gpkg), "--title", title]


def run(paths: Paths, key: str, out_dir: Path | None = None, qgis_python: str | None = None) -> dict[str, Any]:
    city_dir = paths.root / "docs" / "map" / "data" / key
    out_dir = out_dir or paths.root / "docs" / "qgis" / key
    gpkg = out_dir / f"floodline_{key}.gpkg"
    counts = export_city(city_dir, gpkg)
    log.info("qgis %s: %s -> %s", key, ", ".join(f"{k} {v}" for k, v in counts.items()), gpkg)

    title = _title(paths, key)
    script = paths.root / PROJECT_SCRIPT
    launcher = find_qgis_python(qgis_python)
    if launcher is None:
        log.info("QGIS not found; to style the layers and write the project, run with QGIS's Python:")
        log.info("  %s", " ".join(project_command(Path("<qgis python>"), script, gpkg, title)))
        return {"gpkg": str(gpkg), "project": None, "layers": counts}
    subprocess.run(project_command(launcher, script, gpkg, title), check=True)
    project = gpkg.with_suffix(".qgz")
    log.info("qgis %s: project -> %s", key, project)
    return {"gpkg": str(gpkg), "project": str(project), "layers": counts}


def _title(paths: Paths, key: str) -> str:
    """The city's title from the web map's index, so the project and the map carry the same name."""
    cities = paths.root / "docs" / "map" / "cities.json"
    if cities.exists():
        for city in json.loads(cities.read_text(encoding="utf-8")):
            if city.get("key") == key:
                return f"Floodline: {city.get('title', key)}"
    return f"Floodline: {key}"
