"""OpenStreetMap downloads that survive a busy Overpass server.

The public Overpass instances answer 429 / 502 / 504 under load. Every OSM request in the pipeline goes through
``call``: it retries with exponential backoff and moves to the next mirror, and OSMnx's own HTTP cache (``cache/``)
makes a rerun after a failure cheap.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from floodline.log import get_logger

log = get_logger(__name__)

T = TypeVar("T")

OVERPASS_MIRRORS: tuple[str, ...] = (
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
)
ATTEMPTS = 6
BASE_DELAY_S = 10.0


def _transient(exc: Exception) -> bool:
    name = type(exc).__name__
    text = str(exc)
    return (
        name in {"ResponseStatusCodeError", "ConnectionError", "ReadTimeout", "ConnectTimeout", "ChunkedEncodingError"}
        or any(code in text for code in ("429", "502", "503", "504", "Gateway", "timed out"))
        or name == "JSONDecodeError"  # an HTML error page where JSON was expected
    )


def call(fn: Callable[..., T], *args, sleep: Callable[[float], None] = time.sleep, **kwargs) -> T:
    """Run an OSMnx download, retrying transient Overpass failures across mirrors."""
    import osmnx as ox

    last: Exception | None = None
    for attempt in range(ATTEMPTS):
        ox.settings.overpass_url = OVERPASS_MIRRORS[attempt % len(OVERPASS_MIRRORS)]
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # classified below; non-transient errors re-raise
            if not _transient(exc):
                raise
            last = exc
            delay = BASE_DELAY_S * 2**attempt
            log.warning(
                "Overpass %s failed (%s); retry %d/%d in %.0f s",
                ox.settings.overpass_url,
                exc,
                attempt + 1,
                ATTEMPTS,
                delay,
            )
            sleep(delay)
    assert last is not None
    raise last


# ---------------------------------------------------------------- local extracts (no Overpass)
#
# A Geofabrik .osm.pbf snapshot pins the map to a known date, which Overpass (always "now") cannot, and it needs
# no network once downloaded. The two extract functions stream the file once, keep node locations in a disk
# index rather than RAM, and write small .osm XML files that OSMnx reads with graph_from_xml / features_from_xml.

# Same exclusions as OSMnx's "drive" network filter (osmnx._overpass._get_network_filter, osmnx 2.x), so a graph
# built from an extract matches one downloaded from Overpass.
_DRIVE_EXCLUDED_HIGHWAY = {
    "abandoned", "bridleway", "bus_guideway", "construction", "corridor", "cycleway", "elevator", "escalator",
    "footway", "no", "path", "pedestrian", "planned", "platform", "proposed", "raceway", "razed", "rest_area",
    "service", "services", "steps", "track",
}  # fmt: skip
_DRIVE_EXCLUDED_SERVICE = {"alley", "driveway", "emergency_access", "parking", "parking_aisle", "private"}


def is_drivable(tags) -> bool:
    """True for a way OSMnx's ``network_type="drive"`` would keep."""
    highway = tags.get("highway")
    if not highway or highway in _DRIVE_EXCLUDED_HIGHWAY:
        return False
    if tags.get("area") == "yes" or tags.get("access") == "private":
        return False
    if tags.get("motor_vehicle") == "no" or tags.get("motorcar") == "no":
        return False
    return tags.get("service") not in _DRIVE_EXCLUDED_SERVICE


def read_poly(path):
    """An Osmosis .poly boundary (the format Geofabrik publishes next to each extract) as a shapely geometry."""
    from pathlib import Path

    from shapely.geometry import MultiPolygon, Polygon

    lines = [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines()]
    shells, holes, ring, name = [], [], [], None
    for ln in lines[1:]:
        if not ln:
            continue
        if ln == "END":
            if name is None:
                break  # end of file
            (holes if name.startswith("!") else shells).append(ring)
            ring, name = [], None
        elif name is None:
            name = ln
        else:
            x, y = ln.split()[:2]
            ring.append((float(x), float(y)))
    polys = [Polygon(r) for r in shells]
    geom = MultiPolygon(polys) if len(polys) > 1 else polys[0]
    for h in holes:
        geom = geom.difference(Polygon(h))
    return geom


def pbf_covers(pbf, bounds: tuple[float, float, float, float]) -> bool:
    """Whether the extract contains the box ``bounds`` (minx, miny, maxx, maxy, lon/lat).

    Uses the extract's boundary polygon (``<name>.poly`` next to ``<name>.osm.pbf``) when present; the header
    bounding box alone is not enough, since neighbouring regions' boxes overlap.
    """
    from pathlib import Path

    from shapely.geometry import box as make_box

    poly = Path(str(pbf).removesuffix(".osm.pbf") + ".poly")
    if poly.exists():
        return read_poly(poly).contains(make_box(*bounds))
    import osmium

    header = osmium.io.Reader(str(pbf), osmium.osm.osm_entity_bits.NOTHING).header().box()
    minx, miny, maxx, maxy = bounds
    return (
        header.valid()
        and header.bottom_left.lon <= minx
        and header.bottom_left.lat <= miny
        and header.top_right.lon >= maxx
        and header.top_right.lat >= maxy
    )


MIN_PARTIAL_COVER = 0.9


def coverage(pbf, bounds) -> float:
    """Share of the box ``bounds`` inside the extract's .poly boundary (1.0 or 0.0 when there is no .poly)."""
    from pathlib import Path

    from shapely.geometry import box as make_box

    poly = Path(str(pbf).removesuffix(".osm.pbf") + ".poly")
    if not poly.exists():
        return 1.0 if pbf_covers(pbf, bounds) else 0.0
    b = make_box(*bounds)
    return read_poly(poly).intersection(b).area / b.area


def pick_extract(pbfs, bounds):
    """The extract containing ``bounds``; failing that, the one covering most of it if that is >= 90 %.

    The partial case is a road buffer that crosses a regional or national border: roads beyond the border are
    missing from the graph, which the caller logs.
    """
    full = next((p for p in pbfs if pbf_covers(p, bounds)), None)
    if full is not None:
        return full
    best = max(pbfs, key=lambda p: coverage(p, bounds), default=None)
    if best is not None and coverage(best, bounds) >= MIN_PARTIAL_COVER:
        log.warning(
            "%s covers %.0f %% of %s; roads beyond its boundary are missing",
            best.name,
            100 * coverage(best, bounds),
            bounds,
        )
        return best
    return None


def extract(pbf, bounds: tuple[float, float, float, float], out, kind: str):
    """Write the ways of one ``kind`` that touch ``bounds`` from ``pbf`` to ``out`` (.osm), with their nodes.

    ``kind`` is ``"drive"`` (drivable roads) or ``"features"`` (buildings and amenity nodes/ways). Results are
    cached: an existing ``out`` newer than ``pbf`` is reused.
    """
    import gc
    import tempfile
    from pathlib import Path

    import osmium

    pbf, out = Path(pbf), Path(out)
    if out.exists() and out.stat().st_mtime >= pbf.stat().st_mtime:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    minx, miny, maxx, maxy = bounds

    def inside(lon: float, lat: float) -> bool:
        return minx <= lon <= maxx and miny <= lat <= maxy

    keys = ("highway",) if kind == "drive" else ("building", "amenity")
    tmp = out.with_suffix(".part.osm")
    # ignore_cleanup_errors: on Windows the node index can stay locked until the processor is collected.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        index = f"sparse_file_array,{Path(scratch) / 'nodes.idx'}"
        fp = osmium.FileProcessor(str(pbf)).with_locations(index).with_filter(osmium.filter.KeyFilter(*keys))
        with osmium.BackReferenceWriter(str(tmp), ref_src=str(pbf), overwrite=True) as writer:
            for obj in fp:
                if isinstance(obj, osmium.osm.Way):
                    if kind == "drive" and not is_drivable(obj.tags):
                        continue
                    if any(n.location.valid() and inside(n.location.lon, n.location.lat) for n in obj.nodes):
                        writer.add_way(obj)
                elif isinstance(obj, osmium.osm.Node) and kind == "features" and "amenity" in obj.tags:
                    if inside(obj.location.lon, obj.location.lat):
                        writer.add_node(obj)
        del fp  # release the node index before the scratch directory is removed
        gc.collect()
    tmp.replace(out)
    log.info("extract %s -> %s (%.1f MB)", pbf.name, out.name, out.stat().st_size / 1e6)
    return out


class OsmSource:
    """Where OSM data comes from: Overpass (``pbfs`` empty) or local Geofabrik extracts."""

    def __init__(self, pbfs=(), cache_dir=None) -> None:
        from pathlib import Path

        self.pbfs = [Path(p) for p in pbfs]
        self.cache_dir = Path(cache_dir) if cache_dir else None

    @property
    def local(self) -> bool:
        return bool(self.pbfs)

    def describe(self) -> str:
        return "overpass" if not self.local else "extract:" + ",".join(p.name for p in self.pbfs)

    def _file(self, bounds, kind: str):
        pbf = pick_extract(self.pbfs, bounds)
        if pbf is None:
            raise FileNotFoundError(f"no extract among {[p.name for p in self.pbfs]} covers {bounds}")
        if self.cache_dir is None:
            raise ValueError("a cache_dir is needed for local extracts")
        key = "_".join(f"{b:.4f}" for b in bounds)
        return extract(pbf, bounds, self.cache_dir / f"{pbf.stem.split('.')[0]}_{kind}_{key}.osm", kind)

    def graph(self, area, network_type: str = "drive"):
        import osmnx as ox

        if not self.local:
            return call(ox.graph_from_polygon, area, network_type=network_type, truncate_by_edge=True)
        if network_type != "drive":
            raise ValueError("local extracts are filtered for network_type='drive' only")
        g = ox.graph_from_xml(self._file(area.bounds, "drive"), simplify=False, retain_all=True)
        g = ox.truncate.truncate_graph_polygon(g, area, truncate_by_edge=True)
        g = ox.truncate.largest_component(g, strongly=False)  # what graph_from_polygon keeps by default
        return ox.simplify_graph(g)

    def features(self, area, tags):
        """OSM features inside ``area``; an empty GeoDataFrame (not an error) when there are none."""
        import geopandas as gpd
        import osmnx as ox

        try:
            if not self.local:
                return call(ox.features_from_polygon, area, tags)
            return ox.features_from_xml(self._file(area.bounds, "features"), polygon=area, tags=tags)
        except ox._errors.InsufficientResponseError:
            return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
