"""Report trust: which field reports may count as evidence, which need a person to look, which are dropped.

Every field report gets ``trust`` (``accepted`` / ``review`` / ``rejected``) and ``trust_flags`` (why). The rules
are deliberately simple and explainable, because a coordinator has to be able to overrule them:

| Check | Flag | Outcome |
|---|---|---|
| photo cannot be decoded | ``unreadable_photo`` | rejected |
| copy of a published flood photo (CVFD, European Flood 2013), <= 2 bits | ``reused_image`` | rejected |
| near a published flood photo, 3-6 bits | ``possible_reused_image`` | review |
| near-duplicate of an earlier report from the same device | ``duplicate_resend`` | rejected |
| near-duplicate of an earlier report from another device | ``forwarded_photo`` | review |
| capture time more than 10 min after the server received it | ``future_time`` | rejected |
| capture time more than 72 h before receipt | ``stale_photo`` | review |
| more than 10 reports from one device in one hexagon within 10 min | ``device_burst`` | review |

What trust does not decide: whether the flood claim is true. That needs independent evidence, and ``fuse``
handles it: a depth or people claim raises rescue priority only when a second device, the satellite, or the
model's assessment of the photo backs it. An accepted report with no such backing still only raises
*verification* priority.

Near-duplicates use a 64-bit difference hash (dHash): robust to re-compression and resizing, which is what
forwarding through a messaging app does, but not to heavy cropping. A cropped web photo can therefore pass this
check; it still cannot move priority on its own.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)

ACCEPTED, REVIEW, REJECTED = "accepted", "review", "rejected"
NEAR_DUPLICATE_BITS = 6  # of 64; re-encoded and resized copies land at 0-4, unrelated photos near 32
# Rejection needs a near-exact match. Measured on European Flood 2013 vs CVFD (results/trust_eval.json): true
# copies sit at 0-2 bits; only 0.7 % of photos fall at 3-6, so a match there is reviewed, never discarded.
REJECT_BITS = 2
CLOCK_SKEW = timedelta(minutes=10)
STALE_AFTER = timedelta(hours=72)
BURST_WINDOW = timedelta(minutes=10)
BURST_MAX = 10
REFERENCE_INDEX = "reference_hashes.parquet"
_REJECTING = {"unreadable_photo", "reused_image", "duplicate_resend", "future_time"}


def dhash(image, size: int = 8) -> int:
    """Difference hash: compare each pixel with its right neighbour on a (size+1) x size grey thumbnail."""
    from PIL import Image

    im = image if isinstance(image, Image.Image) else Image.open(image)
    grey = np.asarray(im.convert("L").resize((size + 1, size), Image.Resampling.LANCZOS), dtype=np.int16)
    bits = (grey[:, 1:] > grey[:, :-1]).flatten()
    return int(sum(1 << i for i, b in enumerate(bits) if b))


def hamming(a: np.ndarray, b: int) -> np.ndarray:
    """Bit distance from one hash to many (uint64 array)."""
    x = np.bitwise_xor(a.astype(np.uint64), np.uint64(b))
    return np.unpackbits(x.view(np.uint8).reshape(-1, 8), axis=1).sum(axis=1)


def build_reference_index(paths: Paths, roots: Iterable[tuple[str, Path]] | None = None) -> pd.DataFrame:
    """Hashes of every image in the public flood sets we hold, so a field report cannot reuse one of them."""
    roots = roots or (
        ("cvfd", paths.cvfd / "images"),
        ("euflood2013", paths.data / "external" / "eu-flood-2013" / "imgs"),
    )
    rows = []
    for source, root in roots:
        if not root.exists():
            log.warning("reference set %s missing at %s; skipped", source, root)
            continue
        for f in sorted(root.rglob("*")):
            if f.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                continue
            try:
                rows.append({"source": source, "image": f.relative_to(root).as_posix(), "hash": dhash(f)})
            except OSError:
                continue
    df = pd.DataFrame(rows, columns=["source", "image", "hash"])
    df["hash"] = df["hash"].astype("uint64")
    df.to_parquet(paths.data / REFERENCE_INDEX, index=False)
    log.info("reference index: %d images", len(df))
    return df


def assess(
    reports: pd.DataFrame,
    photos_dir: Path,
    reference: pd.DataFrame | None = None,
    resolution: int = 9,
) -> pd.DataFrame:
    """Add ``photo_hash``, ``trust`` and ``trust_flags`` to ingested field reports (one row per report)."""
    import h3

    r = reports.copy().reset_index(drop=True)
    r["captured_at"] = pd.to_datetime(r.captured_at, utc=True)
    r["received_at"] = pd.to_datetime(r.received_at, utc=True)
    r = r.sort_values(["received_at", "id"]).reset_index(drop=True)
    flags: list[set[str]] = [set() for _ in range(len(r))]

    hashes = np.zeros(len(r), dtype=np.uint64)
    readable = np.ones(len(r), dtype=bool)
    for i, path in enumerate(r.photo_path):
        try:
            hashes[i] = np.uint64(dhash(photos_dir / path))
        except (OSError, ValueError):
            readable[i] = False
            flags[i].add("unreadable_photo")
    ref = reference["hash"].to_numpy(dtype=np.uint64) if reference is not None and len(reference) else None

    device = r.get("device_id", pd.Series([None] * len(r))).astype(str)
    for i in range(len(r)):
        if not readable[i]:
            continue
        if ref is not None:
            nearest = int(hamming(ref, int(hashes[i])).min())
            if nearest <= REJECT_BITS:
                flags[i].add("reused_image")
            elif nearest <= NEAR_DUPLICATE_BITS:
                flags[i].add("possible_reused_image")
        earlier = np.flatnonzero(readable[:i])
        if len(earlier):
            close = earlier[hamming(hashes[earlier], int(hashes[i])) <= NEAR_DUPLICATE_BITS]
            if len(close):
                same = (device.iloc[close] == device.iloc[i]).any()
                flags[i].add("duplicate_resend" if same else "forwarded_photo")
        if r.captured_at.iloc[i] > r.received_at.iloc[i] + CLOCK_SKEW:
            flags[i].add("future_time")
        elif r.received_at.iloc[i] - r.captured_at.iloc[i] > STALE_AFTER:
            flags[i].add("stale_photo")

    # Bursts: per device and hexagon, reports beyond BURST_MAX inside a sliding window go to review.
    cells = [h3.latlng_to_cell(a, b, resolution) if pd.notna(a) else None for a, b in zip(r.lat, r.lon, strict=True)]
    r["_cell"] = cells
    for (_, cell), group in r.groupby([device, "_cell"], dropna=True):
        if cell is None or len(group) <= BURST_MAX:
            continue
        times = group.captured_at.to_numpy()
        for pos, idx in enumerate(group.index):
            window = (times[: pos + 1] >= times[pos] - np.timedelta64(int(BURST_WINDOW.total_seconds()), "s")).sum()
            if window > BURST_MAX:
                flags[idx].add("device_burst")

    r["photo_hash"] = hashes
    r["trust_flags"] = [";".join(sorted(f)) for f in flags]
    r["trust"] = [REJECTED if f & _REJECTING else REVIEW if f else ACCEPTED for f in flags]
    return r.drop(columns="_cell")


def run(paths: Paths, build_index: bool = False) -> dict:
    """Build the reference hash index (``--build-index``) and re-assess every ingested report."""
    manifest = RunManifest("trust", params={"build_index": build_index})
    reference = build_reference_index(paths) if build_index else load_reference(paths)
    table = paths.data / "field" / "reports.parquet"
    summary: dict = {"reference_images": 0 if reference is None else len(reference)}
    if table.exists():
        assessed = assess(pd.read_parquet(table), paths.data / "field" / "photos", reference)
        summary["reports"] = len(assessed)
        summary["by_trust"] = assessed.trust.value_counts().to_dict()
    manifest.finish(**summary).write(paths.results / "trust_run.json")
    log.info("trust: %s", summary)
    return summary


def load_reference(paths: Paths) -> pd.DataFrame | None:
    f = paths.data / REFERENCE_INDEX
    return pd.read_parquet(f) if f.exists() else None


def evaluate_hashing(paths: Paths, sample: int = 500, seed: int = 0) -> dict:
    """How well the near-duplicate check works on real flood photos, written to ``results/trust_eval.json``.

    - recall on forwarded copies: half-size, JPEG quality 45, the usual messaging-app treatment;
    - recall on 10 % centre crops: the known weak spot of a difference hash;
    - false matches: distinct reference photos within ``NEAR_DUPLICATE_BITS`` of another one. Exact duplicates
      inside the public sets (same hash) are reported separately, because they are true duplicates.
    """
    import io

    from PIL import Image

    reference = load_reference(paths)
    if reference is None or reference.empty:
        raise FileNotFoundError("reference index missing; run `floodline trust --build-index` first")
    roots = {"cvfd": paths.cvfd / "images", "euflood2013": paths.data / "external" / "eu-flood-2013" / "imgs"}
    rng = np.random.default_rng(seed)
    picks = reference.iloc[rng.choice(len(reference), size=min(sample, len(reference)), replace=False)]
    hashes = reference["hash"].to_numpy(dtype=np.uint64)

    def recompress(im: Image.Image) -> Image.Image:
        buf = io.BytesIO()
        im.convert("RGB").resize((max(1, im.width // 2), max(1, im.height // 2))).save(buf, "JPEG", quality=45)
        return Image.open(io.BytesIO(buf.getvalue()))

    def crop(im: Image.Image) -> Image.Image:
        dx, dy = im.width // 20, im.height // 20
        return im.crop((dx, dy, im.width - dx, im.height - dy))

    found = {"forwarded": 0, "cropped_10pct": 0}
    for row in picks.itertuples(index=False):
        with Image.open(roots[row.source] / row.image) as im:
            im.load()
            for name, transform in (("forwarded", recompress), ("cropped_10pct", crop)):
                if (hamming(hashes, dhash(transform(im))) <= NEAR_DUPLICATE_BITS).any():
                    found[name] += 1

    # Overlap between the two public sets, and leakage between CVFD splits: both are dataset facts worth stating.
    eu = reference[(reference.source == "euflood2013") & reference.image.str.startswith("imgs_large/")]
    cv = reference[reference.source == "cvfd"]
    cv_hashes = cv["hash"].to_numpy(dtype=np.uint64)
    eu_nearest = np.array([hamming(cv_hashes, int(h)).min() for h in eu["hash"].to_numpy(dtype=np.uint64)])
    split = cv.image.str.split("/").str[0]
    train = cv_hashes[(split == "train").to_numpy()]
    leakage = {}
    for name in ("val", "test"):
        part = cv_hashes[(split == name).to_numpy()]
        near = np.array([hamming(train, int(h)).min() for h in part]) if len(train) else np.array([])
        leakage[name] = {"images": len(part), "near_copy_in_train": int((near <= NEAR_DUPLICATE_BITS).sum())}
    exact = int(reference.duplicated("hash", keep=False).sum())
    unique = np.unique(hashes)
    false = 0
    for h in unique[rng.choice(len(unique), size=min(2000, len(unique)), replace=False)]:
        d = hamming(unique, int(h))
        false += int(((d > 0) & (d <= NEAR_DUPLICATE_BITS)).any())
    result = {
        "reference_images": len(reference),
        "sample": len(picks),
        "recall_forwarded": round(found["forwarded"] / len(picks), 3),
        "recall_cropped_10pct": round(found["cropped_10pct"] / len(picks), 3),
        "exact_duplicate_images_in_reference": exact,
        "distinct_hashes_with_a_near_neighbour": round(false / min(2000, len(unique)), 4),
        "threshold_bits": NEAR_DUPLICATE_BITS,
        "reject_bits": REJECT_BITS,
        "euflood_photos": len(eu),
        "euflood_in_cvfd_le2_bits": int((eu_nearest <= REJECT_BITS).sum()),
        "euflood_ambiguous_3_to_6_bits": int(((eu_nearest > REJECT_BITS) & (eu_nearest <= NEAR_DUPLICATE_BITS)).sum()),
        "cvfd_split_leakage": leakage,
    }
    manifest = RunManifest("trust_eval", params={"sample": sample, "seed": seed})
    manifest.finish(**result).write(paths.results / "trust_eval.json")
    log.info("hash evaluation: %s", result)
    return result
