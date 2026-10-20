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
