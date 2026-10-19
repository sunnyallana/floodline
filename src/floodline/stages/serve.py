"""Stage 6: static map outputs.

Writes the ranked hexes as GeoJSON and a self-contained MapLibre page that loads it, so the result can be hosted
as plain files (GitHub Pages, the portfolio site) with no server. For a few thousand hexes GeoJSON is small enough;
PMTiles is the step up when a whole province is covered.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.stages.fuse import hex_geometry

log = get_logger(__name__)

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Floodline · {title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css">
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
<style>
 html,body,#map{{height:100%;margin:0}} .panel{{position:absolute;top:10px;left:10px;background:#fff;padding:10px 12px;font:13px system-ui;border-radius:6px;max-width:320px;box-shadow:0 1px 4px rgba(0,0,0,.2)}}
 .legend span{{display:inline-block;width:12px;height:12px;margin-right:4px;vertical-align:-2px}}
</style></head><body>
<div id="map"></div>
<div class="panel"><b>Floodline</b> · {title}<br><small>{subtitle}</small>
<div class="legend" style="margin-top:6px">priority <span style="background:#ffffb2"></span>low <span style="background:#fd8d3c"></span>mid <span style="background:#bd0026"></span>high</div>
<div id="info" style="margin-top:6px;color:#444">click a hex</div></div>
<script>
const map = new maplibregl.Map({{container:'map', style:'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json', center:[{lon},{lat}], zoom:{zoom}}});
map.on('load', () => {{
  map.addSource('hexes', {{type:'geojson', data:'{geojson}'}});
  map.addLayer({{id:'hex-fill', type:'fill', source:'hexes', paint:{{
    'fill-color':['interpolate',['linear'],['get','priority'],0,'#ffffb2',0.5,'#fd8d3c',1,'#bd0026'],
    'fill-opacity':['interpolate',['linear'],['get','confidence'],0,0.25,1,0.8]}}}});
  map.addLayer({{id:'hex-line', type:'line', source:'hexes', paint:{{'line-color':'#555','line-width':0.5}}}});
  map.on('click','hex-fill', e => {{
    const p = e.features[0].properties;
    document.getElementById('info').innerHTML =
      `priority <b>${{(+p.priority).toFixed(2)}}</b> · confidence ${{(+p.confidence).toFixed(2)}}<br>` +
      `reports ${{p.n_reports}} · max severity ${{p.severity_max ?? '–'}}<br>` +
      `people in water ${{p.people_in_water ?? 0}} · elevated ${{p.people_elevated ?? 0}}<br>` +
      `satellite water ${{p.satellite_water_fraction ?? '–'}} · reach ${{p.reach_minutes ?? '–'}} min`;
  }});
}});
</script></body></html>
"""


def write_geojson(hexes: pd.DataFrame, out: Path) -> Path:
    polys = hex_geometry(hexes.h3)
    features = []
    for (_, row), poly in zip(hexes.iterrows(), polys, strict=True):
        props = {k: (None if pd.isna(v) else (v.isoformat() if hasattr(v, "isoformat") else v)) for k, v in row.items()}
        features.append(
            {"type": "Feature", "geometry": json.loads(json.dumps(poly.__geo_interface__)), "properties": props}
        )
    out.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return out


def run(paths: Paths, hexes_csv: Path, title: str, subtitle: str = "", out_dir: Path | None = None) -> Path:
    import h3

    hexes = pd.read_csv(hexes_csv)
    out_dir = out_dir or paths.results / "map"
    out_dir.mkdir(parents=True, exist_ok=True)
    geojson = write_geojson(hexes, out_dir / "hexes.geojson")
    lat, lon = h3.cell_to_latlng(hexes.h3.iloc[0]) if len(hexes) else (0.0, 0.0)
    page = out_dir / "index.html"
    page.write_text(
        _PAGE.format(title=title, subtitle=subtitle, lat=lat, lon=lon, zoom=11, geojson=geojson.name), encoding="utf-8"
    )
    log.info("map: %s (%d hexes)", page, len(hexes))
    return page
