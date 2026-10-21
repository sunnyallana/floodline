"""Style a Floodline GeoPackage like the web map and save it as a QGIS project beside it.

Run with QGIS's own Python (``floodline qgis <city>`` does this when it finds QGIS):

    "C:/Program Files/QGIS 3.44.14/bin/python-qgis-ltr.bat" qgis/make_project.py \
        docs/qgis/larkana/floodline_larkana.gpkg --title "Floodline: Larkana, Sindh, 2022" [--png preview.png]

Each layer's style is also saved inside the GeoPackage as its default, so the file alone opens styled. The project
stores relative paths: the .qgz and the .gpkg can be moved together to any folder or machine.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from qgis.core import (
    QgsApplication,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsMarkerSymbol,
    QgsProject,
    QgsRasterLayer,
    QgsReferencedRectangle,
    QgsRendererCategory,
    QgsRuleBasedRenderer,
    QgsSingleSymbolRenderer,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor

# The web map's priority ramp (docs/map/index.html, RAMP): class floor, ceiling, colour.
PRIORITY_CLASSES = (
    (0.0, 0.15, "#ffffb2"),
    (0.15, 0.3, "#fecc5c"),
    (0.3, 0.5, "#fd8d3c"),
    (0.5, 0.75, "#f03b20"),
    (0.75, 1.0, "#bd0026"),
)
STAGING_COLOURS = {"hospital": "#1a9850", "fire_station": "#333333"}
OSM_XYZ = "type=xyz&url=https://tile.openstreetmap.org/%7Bz%7D/%7Bx%7D/%7By%7D.png&zmax=19&zmin=0"
WEB_MERCATOR = "EPSG:3857"

# (GeoPackage layer, name in the Layers panel, subset filter), top of the panel first.
LAYERS = (
    ("photos", "Field photos", ""),
    ("staging_points", "Fire stations and hospitals", ""),
    ("closed_roads", "Roads closed by water", ""),
    ("priority_hexes", "Cut off by flooded roads", '"cut_off" = 1'),
    ("priority_hexes", "Priority per hexagon", ""),
    ("mapped_area", "Mapped area (Copernicus EMS)", ""),
    ("flood_extent", "Flood extent (Copernicus EMS)", ""),
)


def priority_renderer() -> QgsRuleBasedRenderer:
    root = QgsRuleBasedRenderer.Rule(None)
    for floor, ceiling, colour in PRIORITY_CLASSES:
        symbol = QgsFillSymbol.createSimple({"color": colour, "outline_color": "0,0,0,40", "outline_width": "0.1"})
        symbol.setOpacity(0.7)  # lets the flood extent show through, as on the web map
        expression = f'"priority" > {floor} AND "priority" <= {ceiling}'
        root.appendChild(QgsRuleBasedRenderer.Rule(symbol, 0, 0, expression, f"{floor:g} to {ceiling:g}"))
    return QgsRuleBasedRenderer(root)


def staging_renderer(layer: QgsVectorLayer) -> QgsCategorizedSymbolRenderer:
    categories = []
    index = layer.fields().indexOf("kind")
    for kind in sorted(str(v) for v in layer.uniqueValues(index)):
        symbol = QgsMarkerSymbol.createSimple(
            {
                "name": "circle",
                "color": "white",
                "outline_color": STAGING_COLOURS.get(kind, "#333333"),
                "outline_width": "0.6",
                "size": "2.8",
            }
        )
        categories.append(QgsRendererCategory(kind, symbol, kind.replace("_", " ").capitalize()))
    return QgsCategorizedSymbolRenderer("kind", categories)


def style(layer: QgsVectorLayer, gpkg_layer: str, subset: str) -> None:
    if subset:  # the cut-off outline: a second view of priority_hexes, drawn above the fills
        layer.setRenderer(
            QgsSingleSymbolRenderer(
                QgsFillSymbol.createSimple({"style": "no", "outline_color": "#111111", "outline_width": "0.6"})
            )
        )
    elif gpkg_layer == "priority_hexes":
        layer.setRenderer(priority_renderer())
    elif gpkg_layer == "flood_extent":
        layer.setRenderer(
            QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "43,123,185,115", "outline_style": "no"}))
        )
    elif gpkg_layer == "mapped_area":
        layer.setRenderer(
            QgsSingleSymbolRenderer(
                QgsLineSymbol.createSimple({"line_color": "#555566", "line_width": "0.4", "line_style": "dash"})
            )
        )
    elif gpkg_layer == "closed_roads":
        layer.setRenderer(
            QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"line_color": "#d7191c", "line_width": "0.7"}))
        )
    elif gpkg_layer == "staging_points":
        layer.setRenderer(staging_renderer(layer))
    elif gpkg_layer == "photos":
        layer.setRenderer(
            QgsSingleSymbolRenderer(
                QgsMarkerSymbol.createSimple(
                    {
                        "name": "circle",
                        "color": "#6a3d9a",
                        "outline_color": "white",
                        "outline_width": "0.3",
                        "size": "2.6",
                    }
                )
            )
        )


def build(gpkg: Path, title: str, png: Path | None) -> Path:
    project = QgsProject.instance()
    project.clear()
    project.setTitle(title)
    crs = QgsCoordinateReferenceSystem(WEB_MERCATOR)
    project.setCrs(crs)
    project.writeEntryBool("Paths", "/Absolute", False)

    present = _layer_names(gpkg)
    tree = project.layerTreeRoot()
    added: list[QgsVectorLayer] = []
    for gpkg_layer, name, subset in LAYERS:
        if gpkg_layer not in present:
            continue
        layer = QgsVectorLayer(f"{gpkg}|layername={gpkg_layer}", name, "ogr")
        if not layer.isValid():
            raise SystemExit(f"{gpkg}: layer {gpkg_layer} did not load")
        if subset:
            layer.setSubsetString(subset)
        style(layer, gpkg_layer, subset)
        # Only the plain view of each table becomes the GeoPackage's default style.
        save_style(gpkg, gpkg_layer, layer, name, default=not subset)
        project.addMapLayer(layer, False)
        tree.addLayer(layer)
        added.append(layer)

    basemap = QgsRasterLayer(OSM_XYZ, "OpenStreetMap", "wms")
    if basemap.isValid():
        project.addMapLayer(basemap, False)
        tree.addLayer(basemap)

    area = next((lyr for lyr in added if lyr.name().startswith("Mapped area")), added[-1])
    extent = QgsCoordinateTransform(area.crs(), crs, project).transformBoundingBox(area.extent())
    extent.scale(1.05)
    project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(extent, crs))

    qgz = gpkg.with_suffix(".qgz")
    if not project.write(str(qgz)):
        raise SystemExit(f"could not write {qgz}")
    if png is not None:
        render(project, extent, png)
    return qgz


def save_style(gpkg: Path, table: str, layer: QgsVectorLayer, name: str, default: bool) -> None:
    """Store the style in the GeoPackage's layer_styles table, replacing a copy from an earlier run."""
    with sqlite3.connect(gpkg) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'layer_styles'").fetchone():
            db.execute("DELETE FROM layer_styles WHERE f_table_name = ? AND styleName = ?", (table, name))
    description = "Floodline web map style"
    if hasattr(layer, "saveStyleToDatabaseV2"):  # QGIS 3.44 and later
        _, error = layer.saveStyleToDatabaseV2(name, description, default, "")
    else:
        error = layer.saveStyleToDatabase(name, description, default, "")
    if error:
        raise SystemExit(f"{gpkg}: could not save the style of {table}: {error}")


def _layer_names(gpkg: Path) -> set[str]:
    from osgeo import ogr

    ogr.UseExceptions()
    ds = ogr.Open(str(gpkg))
    return {ds.GetLayerByIndex(i).GetName() for i in range(ds.GetLayerCount())}


def render(project: QgsProject, extent, png: Path) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gpkg", type=Path)
    parser.add_argument("--title", default="Floodline")
    parser.add_argument("--png", type=Path, help="also render the project's default view to this PNG")
    args = parser.parse_args()
    app = QgsApplication([], False)
    app.initQgis()
    try:
        qgz = build(args.gpkg.resolve(), args.title, args.png)
        print(f"project: {qgz}", flush=True)
    finally:
        QgsProject.instance().clear()  # release QGIS objects before QGIS shuts down
        app.exitQgis()
    return 0


if __name__ == "__main__":
    sys.exit(main())
