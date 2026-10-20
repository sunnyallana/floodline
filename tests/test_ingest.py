from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from floodline import datasets
from floodline.config import Paths
from floodline.location import accuracy_confidence
from floodline.stages import ingest

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _row(i: int, received: str, **over):
    rid = f"00000000-0000-4000-8000-{i:012d}"
    row = {
        "id": rid,
        "team_code": "TEAM-2026-01",
        "captured_at": "2026-10-20T09:00:00+00:00",
        "received_at": received,
        "lat": 50.0123,
        "lon": 14.4011,
        "accuracy_m": 9.0,
        "location_source": "device",
        "people_visible": 3,
        "people_in_water": 1,
        "people_elevated": 2,
        "depth_guess": "waist",
        "note": "boat needed",
        "photo_path": f"{rid}.jpg",
        "photo_bytes": len(JPEG),
        "app_version": "0.1.0",
        "consent": True,
    }
    return {**row, **over}


class FakeStore:
    def __init__(self, rows, photos=None, objects=None):
        self.rows, self.photos, self.objects = rows, photos or {}, objects or []
        self.since_calls, self.deleted = [], []

    def reports_since(self, received_after):
        self.since_calls.append(received_after)
        return [r for r in self.rows if received_after is None or r["received_at"] >= received_after]

    def photo(self, path):
        return self.photos.get(path, JPEG)

    def all_photo_paths(self):
        return {r["photo_path"] for r in self.rows}

    def list_objects(self):
        return self.objects

    def delete_objects(self, names):
        self.deleted += names


@pytest.fixture
def paths(tmp_path):
    p = Paths(root=tmp_path)
    p.data.mkdir(parents=True)
    p.results.mkdir(parents=True)
    return p


def test_ingest_writes_photos_table_and_fuse_csv(paths):
    store = FakeStore(
        [
            _row(1, "2026-10-20T09:01:00+00:00"),
            _row(2, "2026-10-20T09:02:00+00:00", lat=None, lon=None, accuracy_m=None, location_source="none"),
        ]
    )
    summary = ingest.run(paths, store)
    assert summary["new_reports"] == 2 and summary["with_location"] == 1
    field = paths.data / "field"
    assert (field / "photos" / f"{_row(1, '')['id']}.jpg").read_bytes() == JPEG
    fuse = pd.read_csv(field / "reports_for_fuse.csv")
    located = fuse.dropna(subset=["lat"]).iloc[0]
    assert located.location_confidence == pytest.approx(0.95) and located.depth_class == "waist"
    assert located.people_on_roofs_or_elevated == 2 and located.confidence == ingest.REPORTED_DEPTH_CONFIDENCE
    assert fuse.location_confidence.min() == 0.0  # the report without a location never gets a point
    manifest = (paths.results / "ingest_run.json").read_text("utf-8")
    assert "50.01" not in manifest and "boat needed" not in manifest  # the committed manifest holds counts only


def test_ingest_is_incremental_and_idempotent(paths):
    store = FakeStore([_row(1, "2026-10-20T09:01:00+00:00")])
    ingest.run(paths, store)
    store.rows.append(_row(2, "2026-10-20T09:05:00+00:00"))
    summary = ingest.run(paths, store)
    assert store.since_calls[-1] == "2026-10-20T09:01:00+00:00"  # asks only for what is newer
    assert summary == {**summary, "new_reports": 1, "total_reports": 2}
    assert ingest.run(paths, store)["new_reports"] == 0


def test_non_jpeg_photos_are_rejected(paths):
    bad = _row(3, "2026-10-20T09:01:00+00:00")
    store = FakeStore([bad], photos={bad["photo_path"]: b"<html>not a photo</html>"})
    summary = ingest.run(paths, store)
    assert summary["rejected_non_jpeg"] == 1 and summary["new_reports"] == 0
    assert not (paths.data / "field" / "photos" / bad["photo_path"]).exists()


def test_orphans_only_after_the_grace_period():
    now = datetime(2026, 10, 21, 12, tzinfo=UTC)
    objects = [
        {"name": "a.jpg", "created_at": (now - timedelta(hours=30)).isoformat()},
        {"name": "b.jpg", "created_at": (now - timedelta(hours=2)).isoformat()},  # report may still be uploading
        {"name": "c.jpg", "created_at": (now - timedelta(days=3)).isoformat().replace("+00:00", "Z")},
    ]
    assert ingest.orphans(objects, known={"c.jpg"}, now=now) == ["a.jpg"]


def test_prune_deletes_orphans(paths):
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    store = FakeStore([_row(1, "2026-10-20T09:01:00+00:00")], objects=[{"name": "stray.jpg", "created_at": old}])
    assert ingest.run(paths, store, prune_orphans=True)["orphan_photos_deleted"] == 1
    assert store.deleted == ["stray.jpg"]


def test_field_dataset_points_at_ingested_photos(paths):
    ingest.run(paths, FakeStore([_row(1, "2026-10-20T09:01:00+00:00")]))
    ds = datasets.load(paths, "field")
    assert ds.image(ds.frame.iloc[0]).read_bytes() == JPEG and not ds.has_masks


@pytest.mark.parametrize(
    ("accuracy", "source", "expected"),
    [
        (8, "device", 0.95),
        (60, "device", 0.8),
        (250, "exif", 0.6),
        (900, "device", 0.4),
        (5000, "device", 0.2),
        (None, "exif", 0.8),
        (10, "none", 0.0),
    ],
)
def test_accuracy_confidence(accuracy, source, expected):
    assert accuracy_confidence(accuracy, source) == expected


def test_store_needs_credentials(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        ingest.SupabaseStore.from_env()
