"""Stage 0: pull field reports from the Floodline Field app's Supabase project into the pipeline.

The app writes with the public anon key, which may only insert (see ``supabase/migrations``). This stage reads
with the service-role key, so it runs only on the analysis machine, from ``SUPABASE_URL`` and
``SUPABASE_SERVICE_KEY`` in the environment, never from a file in the repo.

Outputs, all under ``data/field/`` (git-ignored: these are photos of people and exact positions):

- ``photos/<id>.jpg``: the uploaded photo, checked to be a JPEG;
- ``reports.parquet``: every report ever ingested, one row per id;
- ``reports_for_fuse.csv``: the same reports in the shape ``floodline fuse --reports`` reads, each with a
  ``trust`` verdict and its reasons from ``stages/trust.py``.

The public outputs of the pipeline only ever see these through the hex aggregation in ``fuse``.
Runs are incremental: only reports received after the newest one already stored are requested.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from floodline.config import Paths
from floodline.location import accuracy_confidence, h3_index
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

BUCKET = "field-photos"
PAGE = 1000
REPORTED_DEPTH_CONFIDENCE = 0.5  # a responder's own depth tag, before a model has looked at the photo
JPEG_MAGIC = b"\xff\xd8\xff"
ORPHAN_MIN_AGE = timedelta(hours=24)


class Store(Protocol):
    def reports_since(self, received_after: str | None) -> Iterable[dict[str, Any]]: ...
    def photo(self, path: str) -> bytes: ...
    def all_photo_paths(self) -> set[str]: ...
    def list_objects(self) -> list[dict[str, Any]]: ...
    def delete_objects(self, names: list[str]) -> None: ...
    def devices(self) -> list[dict[str, Any]]: ...


@dataclass
class SupabaseStore:
    """The two Supabase APIs this stage needs, over plain HTTPS: PostgREST for rows, Storage for photos."""

    url: str
    service_key: str
    timeout_s: float = 60.0

    @classmethod
    def from_env(cls) -> SupabaseStore:
        url, key = os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")
        if not url.startswith("https://") or not key:
            raise RuntimeError("set SUPABASE_URL (https://<ref>.supabase.co) and SUPABASE_SERVICE_KEY to ingest")
        return cls(url.rstrip("/"), key)

    def _session(self):
        import requests

        s = requests.Session()
        s.headers.update({"apikey": self.service_key, "Authorization": f"Bearer {self.service_key}"})
        return s

    def reports_since(self, received_after: str | None) -> Iterable[dict[str, Any]]:
        s = self._session()
        cursor = received_after
        while True:
            params = {"select": "*", "order": "received_at.asc,id.asc", "limit": str(PAGE)}
            if cursor:
                params["received_at"] = f"gte.{cursor}"  # gte + id dedupe: rows sharing a timestamp are not lost
            r = s.get(f"{self.url}/rest/v1/reports", params=params, timeout=self.timeout_s)
            r.raise_for_status()
            rows = r.json()
            yield from rows
            if len(rows) < PAGE:
                return
            last = rows[-1]["received_at"]
            if last == cursor:
                raise RuntimeError(f"more than {PAGE} reports share received_at={last}; raise PAGE")
            cursor = last

    def photo(self, path: str) -> bytes:
        r = self._session().get(f"{self.url}/storage/v1/object/authenticated/{BUCKET}/{path}", timeout=self.timeout_s)
        r.raise_for_status()
        return r.content

    def all_photo_paths(self) -> set[str]:
        s = self._session()
        paths: set[str] = set()
        offset = 0
        while True:
            r = s.get(
                f"{self.url}/rest/v1/reports",
                params={"select": "photo_path", "order": "id.asc", "limit": str(PAGE), "offset": str(offset)},
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            rows = r.json()
            paths |= {row["photo_path"] for row in rows}
            if len(rows) < PAGE:
                return paths
            offset += PAGE

    def list_objects(self) -> list[dict[str, Any]]:
        s = self._session()
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            r = s.post(
                f"{self.url}/storage/v1/object/list/{BUCKET}",
                json={"prefix": "", "limit": PAGE, "offset": offset, "sortBy": {"column": "name", "order": "asc"}},
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            batch = r.json()
            out += batch
            if len(batch) < PAGE:
                return out
            offset += PAGE

    def devices(self) -> list[dict[str, Any]]:
        """Enrolled phones and whether they are still active (see ``field_admin``)."""
        r = self._session().get(f"{self.url}/rest/v1/devices", params={"select": "id,active"}, timeout=self.timeout_s)
        r.raise_for_status()
        return list(r.json())

    def delete_objects(self, names: list[str]) -> None:
        if names:
            r = self._session().delete(f"{self.url}/storage/v1/object/{BUCKET}", json={"prefixes": names})
            r.raise_for_status()


def for_fuse(reports: pd.DataFrame) -> pd.DataFrame:
    """Field reports in the column layout ``fuse.aggregate`` reads."""
    if reports.empty:
        return pd.DataFrame(columns=["image_id", "lat", "lon", "location_confidence", "captured_at"])
    out = pd.DataFrame(
        {
            "image_id": "field_" + reports.id.astype(str),
            "source": "field",
            "device_id": reports.get("device_id"),
            "trust": reports.get("trust", "accepted"),
            "trust_flags": reports.get("trust_flags", ""),
            "lat": reports.lat,
            "lon": reports.lon,
            "location_tier": reports.location_source.map({"device": "gps", "exif": "gps"}).fillna("none"),
            "location_confidence": [
                accuracy_confidence(a, s) for a, s in zip(reports.accuracy_m, reports.location_source, strict=True)
            ],
            "h3": [h3_index(a, b) if pd.notna(a) else None for a, b in zip(reports.lat, reports.lon, strict=True)],
            "captured_at": reports.captured_at,
            "people_visible": reports.people_visible,
            "people_in_water": reports.people_in_water,
            "people_on_roofs_or_elevated": reports.people_elevated,
            "depth_class": reports.depth_guess,
            "confidence": REPORTED_DEPTH_CONFIDENCE,
            "note": reports.note,
            "image_path": "photos/" + reports.photo_path,
        }
    )
    return out


def orphans(
    objects: list[dict[str, Any]], known: set[str], now: datetime, min_age: timedelta = ORPHAN_MIN_AGE
) -> list[str]:
    """Photos with no report that are old enough that their report is not still on its way."""
    stale = []
    for o in objects:
        name, created = o.get("name"), o.get("created_at")
        if not name or name in known or not created:
            continue
        when = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        if now - when >= min_age:
            stale.append(name)
    return stale


def _write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def run(paths: Paths, store: Store | None = None, prune_orphans: bool = False, now: datetime | None = None) -> dict:
    store = store or SupabaseStore.from_env()
    now = now or datetime.now(UTC)
    root = paths.data / "field"
    photos = root / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    table = root / "reports.parquet"
    manifest = RunManifest("ingest", params={"prune_orphans": prune_orphans})

    existing = pd.read_parquet(table) if table.exists() else pd.DataFrame()
    since = str(existing.received_at.max()) if len(existing) else None
    known_ids = set(existing.id.astype(str)) if len(existing) else set()

    fresh, bad_photos = [], []
    for row in store.reports_since(since):
        if str(row["id"]) in known_ids:
            continue
        data = store.photo(row["photo_path"])
        if not data.startswith(JPEG_MAGIC):
            bad_photos.append(row["id"])
            continue
        _write_atomic(photos / row["photo_path"], data)
        fresh.append(row)
        known_ids.add(str(row["id"]))

    new = pd.DataFrame(fresh)
    combined = pd.concat([existing, new], ignore_index=True) if len(new) else existing
    if len(new):
        combined = combined.drop_duplicates("id", keep="first").sort_values("received_at").reset_index(drop=True)
        combined.to_parquet(table, index=False)
    from floodline.stages import trust
    from floodline.stages.field_admin import revoked_ids

    # Withdrawals apply retroactively: every run re-checks all stored reports against the current device list.
    withdrawn = revoked_ids(store.devices())
    checked = (
        trust.assess(combined, photos, trust.load_reference(paths), withdrawn=withdrawn) if len(combined) else combined
    )
    for_fuse(checked).to_csv(root / "reports_for_fuse.csv", index=False)

    pruned: list[str] = []
    if prune_orphans:
        pruned = orphans(store.list_objects(), store.all_photo_paths(), now)
        store.delete_objects(pruned)

    summary = {
        "new_reports": len(new),
        "total_reports": len(combined),
        "with_location": int(combined.lat.notna().sum()) if len(combined) else 0,
        "rejected_non_jpeg": len(bad_photos),
        "trust": checked.trust.value_counts().to_dict() if len(checked) else {},
        "orphan_photos_deleted": len(pruned),
        "withdrawn_devices": len(withdrawn),
    }
    # Counts only: the manifest is committed, the reports are not.
    manifest.finish(**summary).write(paths.results / "ingest_run.json")
    (root / "last_ingest.json").write_text(json.dumps({"at": now.isoformat(), **summary}), encoding="utf-8")
    log.info("ingest: %s", summary)
    return summary
