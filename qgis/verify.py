"""Check a Floodline QGIS project the way a user would meet it, in QGIS's own Python:

    "C:/Program Files/QGIS 3.44.14/bin/python-qgis-ltr.bat" qgis/verify.py docs/qgis/prague/floodline_prague.qgz \
        [--png preview.png]

- the project opens and every layer in it loads, with paths stored relative to the project;
- each GeoPackage table carries its default style, so the file alone opens styled;
- the Re-rank hexagons tool, at its default weights, reproduces the published priority of every hexagon;
- optionally, the project's default view renders to a PNG.

Exits non-zero on the first failure. tests/test_qgis.py runs it when FLOODLINE_QGIS_PYTHON points at QGIS.
"""

from __future__ import annotations

import argparse
import gc
import os
import sqlite3
import sys
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (
    QgsApplication,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProject,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor

HERE = Path(__file__).resolve().parent


def fail(message: str) -> None:
    print(f"FAIL: {message}", flush=True)
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok  {message}", flush=True)  # flushed: a crash in QGIS teardown must not swallow the result


def check_project(qgz: Path) -> QgsProject:
    with zipfile.ZipFile(qgz) as z:
        qgs = next(n for n in z.namelist() if n.endswith(".qgs"))
        xml = z.read(qgs).decode("utf-8")
    if str(qgz.parent.resolve()).replace("\\", "/") in xml.replace("\\", "/"):
        fail("the project stores absolute paths")
    project = QgsProject.instance()
    if not project.read(str(qgz)):
        fail(f"{qgz} does not open")
    layers = list(project.mapLayers().values())
    broken = [lyr.name() for lyr in layers if not lyr.isValid()]
    if broken:
        fail(f"layers that do not load: {broken}")
    ok(f"project opens, {len(layers)} layers load, relative paths")
    return project


def check_styles(gpkg: Path) -> None:
    with sqlite3.connect(gpkg) as db:
        tables = {r[0] for r in db.execute("SELECT table_name FROM gpkg_contents WHERE data_type = 'features'")}
        styled = {r[0] for r in db.execute("SELECT f_table_name FROM layer_styles WHERE useAsDefault = 1")}
        per_table = db.execute(
            "SELECT f_table_name, COUNT(*) FROM layer_styles WHERE useAsDefault = 1 GROUP BY f_table_name"
        ).fetchall()
    if tables - styled:
        fail(f"tables without a default style: {sorted(tables - styled)}")
    doubled = [t for t, n in per_table if n > 1]
    if doubled:
        fail(f"tables with more than one default style: {doubled}")
    ok(f"{len(tables)} tables each carry one default style")


def check_rerank(gpkg: Path) -> None:
    sys.path.insert(0, str(HERE))
    from floodline_rerank import RerankHexagons, _number, fused_priority

    hexes = QgsVectorLayer(f"{gpkg}|layername=priority_hexes", "hexes", "ogr")
    # Plain Python values, so nothing compared below holds on to a QGIS feature.
    published = {str(f["h3"]): _number(f["priority"]) for f in hexes.getFeatures()}
    weights = (0.45, 0.30, 0.25)
    worst = max(
        abs(
            fused_priority(
                f["exposure"],
                f["hazard"],
                f["access"],
                f["cut_off"],
                *weights,
                water=f["satellite_water_fraction"],
                published=f["priority"],
            )
            - _number(f["priority"])
        )
        for f in hexes.getFeatures()
    )

    algorithm = RerankHexagons()
    algorithm.initAlgorithm()
    context, feedback = QgsProcessingContext(), QgsProcessingFeedback()
    params = {"INPUT": hexes, "W_EXPOSURE": 0.45, "W_HAZARD": 0.30, "W_ACCESS": 0.25, "TOP_N": 10,
              "OUTPUT": "memory:"}  # fmt: skip
    valid, msg = algorithm.checkParameterValues(params, context)
    if not valid:
        fail(f"rerank rejects its own defaults: {msg}")
    result = algorithm.run(params, context, feedback)
    out = result[0] if isinstance(result, tuple) else result
    layer = context.getMapLayer(out["OUTPUT"]) if isinstance(out["OUTPUT"], str) else out["OUTPUT"]
    rows = sorted((int(f["rank_custom"]), str(f["h3"])) for f in layer.getFeatures())
    if len(rows) != min(10, len(published)):
        fail(f"top 10 returned {len(rows)} hexagons")
    if worst > 1.5e-3:
        fail(f"default weights move a published priority by {worst:.4f}")
    by_priority = sorted(published, key=lambda h: -published[h])
    tied_at_cut = len(by_priority) > 10 and published[by_priority[9]] == published[by_priority[10]]
    if not tied_at_cut and {h for _, h in rows} != set(by_priority[:10]):
        fail("the re-ranked top 10 differs from the published top 10")
    ok(f"rerank at default weights matches the published priority (worst {worst:.4f}); top 10 returned")


def render(project: QgsProject, png: Path) -> None:
    extent = project.viewSettings().defaultViewExtent()
    settings = QgsMapSettings()
    settings.setLayers([node.layer() for node in project.layerTreeRoot().findLayers()])
    settings.setDestinationCrs(project.crs())
    settings.setExtent(extent)
    settings.setOutputSize(QSize(1600, int(1600 * extent.height() / extent.width())))
    settings.setBackgroundColor(QColor("white"))
    job = QgsMapRendererParallelJob(settings)
    job.start()
    job.waitForFinished()
    job.renderedImage().save(str(png), "PNG")
    ok(f"rendered {png}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("qgz", type=Path)
    parser.add_argument("--png", type=Path)
    args = parser.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    try:
        qgz = args.qgz.resolve()
        project = check_project(qgz)
        gpkg = qgz.with_suffix(".gpkg")
        check_styles(gpkg)
        check_rerank(gpkg)
        if args.png:
            render(project, args.png)
    finally:
        # Release every QGIS object Python still holds before QGIS shuts down; freeing them afterwards crashes.
        QgsProject.instance().clear()
        gc.collect()
        app.exitQgis()
    return 0


if __name__ == "__main__":
    sys.exit(main())
