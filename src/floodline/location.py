"""Location tiers: where a photo was taken, and how sure we are.

Tier 1 is the camera's own GPS in the file. Tier 2 is a place read from the image (sign text, landmark) and
geocoded. Tier 3 is the report's own text or a district name. A report with no tier gets a depth class but no
point on the map; nothing is ever inferred beyond the tier that produced it.

Coordinates are indexed to H3 cells at the publication resolution so that raw points never leave the private
store; the public layers carry hex statistics only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from floodline.schema import LocationTier

H3_RESOLUTION = 8  # ~0.74 km² cells: fine enough to send a boat to, coarse enough to protect a household
TIER_CONFIDENCE: dict[LocationTier, float] = {
    "gps": 0.95,
    "visual_text": 0.6,
    "visual_landmark": 0.5,
    "report_text": 0.4,
    "district": 0.2,
    "none": 0.0,
}

_GPS_IFD = 0x8825
_GPS_LAT_REF, _GPS_LAT, _GPS_LON_REF, _GPS_LON = 1, 2, 3, 4
_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME = 306


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    tier: LocationTier
    confidence: float
    h3_cell: str | None = None
    captured_at: datetime | None = None

    @classmethod
    def from_tier(cls, lat: float, lon: float, tier: LocationTier, captured_at: datetime | None = None) -> Location:
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise ValueError(f"coordinates out of range: {lat}, {lon}")
        return cls(lat, lon, tier, TIER_CONFIDENCE[tier], h3_index(lat, lon), captured_at)


def h3_index(lat: float, lon: float, resolution: int = H3_RESOLUTION) -> str | None:
    """H3 cell for a point, or ``None`` when the h3 extra is not installed (the base package stays light)."""
    try:
        import h3
    except ImportError:
        return None
    return h3.latlng_to_cell(lat, lon, resolution)


def _to_degrees(value) -> float:
    d, m, s = (float(v) for v in value)
    return d + m / 60.0 + s / 3600.0


def exif_location(path: Path) -> Location | None:
    """Tier-1 location from EXIF GPS, with the capture time when present. ``None`` if the file has no GPS."""
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            gps = exif.get_ifd(_GPS_IFD) if exif else None
            if not gps or _GPS_LAT not in gps or _GPS_LON not in gps:
                return None
            lat = _to_degrees(gps[_GPS_LAT])
            lon = _to_degrees(gps[_GPS_LON])
            if str(gps.get(_GPS_LAT_REF, "N")).upper() == "S":
                lat = -lat
            if str(gps.get(_GPS_LON_REF, "E")).upper() == "W":
                lon = -lon
            captured = exif.get_ifd(0x8769).get(_EXIF_DATETIME_ORIGINAL) or exif.get(_EXIF_DATETIME)
    except (OSError, ValueError, TypeError, ZeroDivisionError):
        return None
    when = None
    if isinstance(captured, str):
        try:
            when = datetime.strptime(captured, "%Y:%m:%d %H:%M:%S")
        except ValueError:
            when = None
    try:
        return Location.from_tier(lat, lon, "gps", when)
    except ValueError:
        return None


def accuracy_confidence(accuracy_m: float | None, source: str) -> float:
    """Location confidence for a field report from the reported horizontal accuracy of its GPS fix.

    The field app sends ``device`` (the phone's fix at capture) or ``exif`` (GPS the camera wrote into the photo).
    A fix within 25 m is as good as tier-1 EXIF GPS; a fix of several hundred metres can put the report in the
    neighbouring hexagon, so confidence falls with it. A camera fix without a stated accuracy is treated as a
    typical phone fix.
    """
    if source not in ("device", "exif"):
        return 0.0
    if accuracy_m is None or accuracy_m != accuracy_m:  # NaN
        return 0.8
    for limit, confidence in ((25.0, TIER_CONFIDENCE["gps"]), (100.0, 0.8), (300.0, 0.6), (1000.0, 0.4)):
        if accuracy_m <= limit:
            return confidence
    return 0.2
