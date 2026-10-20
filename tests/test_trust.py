import io

import numpy as np
import pandas as pd
import pytest
from PIL import Image, ImageDraw

from floodline.config import Paths
from floodline.stages import trust

pytest.importorskip("h3")


def _scene(seed: int, size=(640, 480)) -> Image.Image:
    """A distinct synthetic 'photo': random rectangles on a gradient."""
    rng = np.random.default_rng(seed)
    im = Image.new("RGB", size, (int(rng.integers(0, 255)), 120, 160))
    d = ImageDraw.Draw(im)
    for _ in range(25):
        x, y = int(rng.integers(0, size[0])), int(rng.integers(0, size[1]))
        d.rectangle(
            [x, y, x + int(rng.integers(20, 200)), y + int(rng.integers(20, 160))],
            fill=tuple(int(v) for v in rng.integers(0, 255, 3)),
        )
    return im


def _forwarded(im: Image.Image) -> Image.Image:
    """What a messaging app does: downscale and re-encode at low quality."""
    small = im.resize((im.width // 2, im.height // 2))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=45)
    return Image.open(io.BytesIO(buf.getvalue()))


def test_dhash_survives_forwarding_and_separates_different_scenes():
    a = _scene(1)
    same = trust.hamming(np.array([trust.dhash(a)], dtype=np.uint64), trust.dhash(_forwarded(a)))[0]
    other = trust.hamming(np.array([trust.dhash(a)], dtype=np.uint64), trust.dhash(_scene(2)))[0]
    assert same <= trust.NEAR_DUPLICATE_BITS < other


def _report(
    i: int, photo: str, device="d1", captured="2026-10-20T09:00:00Z", received="2026-10-20T09:05:00Z", lat=50.0
):
    return {
        "id": f"00000000-0000-4000-8000-{i:012d}",
        "device_id": device,
        "captured_at": captured,
        "received_at": received,
        "lat": lat,
        "lon": 14.4,
        "photo_path": photo,
    }


@pytest.fixture
def photos(tmp_path):
    d = tmp_path / "photos"
    d.mkdir()
    _scene(10).save(d / "orig.jpg", quality=90)
    _forwarded(_scene(10)).save(d / "copy.jpg")
    _scene(11).save(d / "other.jpg", quality=90)
    _scene(12).save(d / "web.jpg", quality=90)
    (d / "broken.jpg").write_bytes(b"\xff\xd8\xff not really a jpeg")
    return d


def _verdicts(df):
    return dict(zip(df.photo_path, zip(df.trust, df.trust_flags, strict=True), strict=True))


def test_duplicates_reused_images_and_unreadable_photos(photos):
    reference = pd.DataFrame(
        {"source": ["euflood2013"], "image": ["x.jpg"], "hash": [np.uint64(trust.dhash(photos / "web.jpg"))]}
    )
    reports = pd.DataFrame(
        [
            _report(1, "orig.jpg", device="d1"),
            _report(2, "copy.jpg", device="d2", received="2026-10-20T09:06:00Z"),  # same scene from another phone
            _report(3, "other.jpg"),
            _report(4, "web.jpg"),
            _report(5, "broken.jpg"),
        ]
    )
    v = _verdicts(trust.assess(reports, photos, reference))
    assert v["orig.jpg"] == ("accepted", "")
    assert v["copy.jpg"] == ("review", "forwarded_photo")
    assert v["other.jpg"] == ("accepted", "")
    assert v["web.jpg"] == ("rejected", "reused_image")
    assert v["broken.jpg"] == ("rejected", "unreadable_photo")


def test_a_resend_from_the_same_device_is_dropped(photos):
    reports = pd.DataFrame([_report(1, "orig.jpg"), _report(2, "copy.jpg", received="2026-10-20T09:07:00Z")])
    v = _verdicts(trust.assess(reports, photos))
    assert v["copy.jpg"] == ("rejected", "duplicate_resend")


def test_timestamps(photos):
    reports = pd.DataFrame(
        [
            _report(
                1, "orig.jpg", captured="2026-10-20T10:00:00Z", received="2026-10-20T09:00:00Z"
            ),  # 1 h in the future
            _report(2, "other.jpg", captured="2026-10-15T09:00:00Z", received="2026-10-20T09:00:00Z"),  # 5 days old
        ]
    )
    v = _verdicts(trust.assess(reports, photos))
    assert v["orig.jpg"] == ("rejected", "future_time") and v["other.jpg"] == ("review", "stale_photo")


def test_bursts_from_one_device_in_one_hexagon_go_to_review(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    rows = []
    for i in range(trust.BURST_MAX + 3):
        _scene(100 + i).save(d / f"{i}.jpg", quality=90)
        t = f"2026-10-20T09:{i:02d}:00Z"
        rows.append(_report(i, f"{i}.jpg", captured=t, received=t))
    out = trust.assess(pd.DataFrame(rows), d)
    assert (out.trust == "accepted").sum() == trust.BURST_MAX
    assert (out.trust_flags == "device_burst").sum() == 3


def test_reference_index_round_trip(tmp_path):
    paths = Paths(root=tmp_path)
    paths.data.mkdir(parents=True)
    root = tmp_path / "ref"
    (root / "sub").mkdir(parents=True)
    _scene(5).save(root / "sub" / "a.jpg")
    df = trust.build_reference_index(paths, roots=[("test", root)])
    assert df.image.tolist() == ["sub/a.jpg"] and df.hash.dtype == np.uint64
    assert trust.load_reference(paths).equals(df)


def test_reference_matches_are_tiered(tmp_path):
    d = tmp_path / "p"
    d.mkdir()
    _scene(20).save(d / "a.jpg", quality=90)
    h = trust.dhash(d / "a.jpg")
    exact = pd.DataFrame({"source": ["x"], "image": ["r"], "hash": [np.uint64(h)]})
    near = pd.DataFrame({"source": ["x"], "image": ["r"], "hash": [np.uint64(h ^ 0b111)]})  # 3 bits away
    reports = pd.DataFrame([_report(1, "a.jpg")])
    assert trust.assess(reports, d, exact).trust.iloc[0] == "rejected"
    flagged = trust.assess(reports, d, near).iloc[0]
    assert flagged.trust == "review" and flagged.trust_flags == "possible_reused_image"
