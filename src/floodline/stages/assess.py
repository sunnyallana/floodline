"""Stage 3: structured flood assessment per photo with an open-weight VLM served by llama.cpp.

Each record either carries a validated ``assessment`` or an ``error`` with the raw text, so the failure rate is a
reported number rather than a silent drop. Resumable through the JSONL store.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image
from pydantic import ValidationError

from floodline import datasets
from floodline.config import ModelIds, Paths, default_threads
from floodline.depth import bin_depth, estimate_from_level
from floodline.llama import LlamaServer, upscale
from floodline.log import get_logger
from floodline.reading_rules import read_from_text
from floodline.resume import JsonlStore
from floodline.runs import RunManifest
from floodline.schema import DESCRIBE_PROMPT, READ_DEPTH_PROMPT, DepthReading, DerivedDepth, PhotoAssessment

log = get_logger(__name__)
_JSON_OBJECT = re.compile(r"\{.*\}", re.S)


def _json_object(raw: str) -> str:
    m = _JSON_OBJECT.search(raw)
    if not m:
        raise ValueError("no JSON object in model output")
    return m.group(0)


def parse_assessment(raw: str) -> PhotoAssessment:
    return PhotoAssessment.model_validate_json(_json_object(raw))


def parse_reading(raw: str) -> DepthReading:
    return DepthReading.model_validate_json(_json_object(raw))


def derive_depth(a: PhotoAssessment, reading: DepthReading | None) -> DerivedDepth:
    """Depth class from the pass-2 (reference object, water level) pair. The model never emits a class itself."""
    if reading is None or a.aerial_view or reading.reference_object == "none":
        return DerivedDepth(depth_class="unknown")
    est = estimate_from_level(reading.reference_object, reading.water_level)
    if est is None:
        return DerivedDepth(depth_class="unknown")
    if reading.water_level == "not_touching":
        return DerivedDepth(depth_class="dry", depth_cm_low=0.0, depth_cm_high=0.0)
    return DerivedDepth(
        depth_class=bin_depth(est.depth_cm_mid),
        depth_cm_low=round(est.depth_cm_low, 1),
        depth_cm_high=round(est.depth_cm_high, 1),
    )


def _value_counts(values: list[str]) -> dict[str, int]:
    return pd.Series(values, dtype=str).value_counts().to_dict() if values else {}


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if "assessment" in r]
    lat = pd.Series([r["latency_s"] for r in records if "latency_s" in r], dtype=float)
    return {
        "images": len(records),
        "valid_json": len(ok),
        "failure_rate": round(1 - len(ok) / len(records), 4) if records else None,
        "median_latency_s": round(float(lat.median()), 2) if len(lat) else None,
        "depth_class_counts": pd.Series([r["depth"]["depth_class"] for r in ok if "depth" in r])
        .value_counts()
        .to_dict()
        if ok
        else {},
    }


def run(
    paths: Paths,
    dataset: str = "cvfd",
    model: str = ModelIds.vlm_gguf,
    mmproj: str = ModelIds.vlm_mmproj,
    limit: int | None = None,
    threads: int | None = None,
    port: int = 8089,
    size: int = 672,
    out: Path | None = None,
) -> dict[str, Any]:
    threads = threads or default_threads()
    ds = datasets.load(paths, dataset, limit=limit)
    ev = ds.frame
    store = JsonlStore(out or paths.vlm_assessments)
    done = store.done()
    describe_schema = PhotoAssessment.model_json_schema()
    reading_schema = DepthReading.model_json_schema()
    manifest = RunManifest(
        "assess",
        params={
            "model": model,
            "mmproj": mmproj,
            "threads": threads,
            "input_px": size,
            "device": "cpu",
            "prompt_sha": _sha(DESCRIBE_PROMPT + READ_DEPTH_PROMPT),
            "passes": 2,
        },
    )
    server = LlamaServer(
        paths.tools / "llama" / "llama-server.exe",
        paths.models / model,
        paths.models / mmproj,
        port=port,
        threads=threads,
        log_path=paths.results / "llama_server.log",
    )
    new = 0
    with server:
        for _, r in ev.iterrows():
            if r.image_id in done:
                continue
            rec: dict[str, Any] = {"image_id": r.image_id, "model": model, "input_px": size}
            raw = None
            try:
                image = upscale(Image.open(ds.image(r)).convert("RGB"), size)
                raw, dt1 = server.chat_json(image, DESCRIBE_PROMPT, describe_schema)
                assessment = parse_assessment(raw)
                rec["assessment"] = assessment.model_dump()
                reading = None
                dt2 = 0.0
                if assessment.is_flood_scene and not assessment.aerial_view:
                    ruled = read_from_text(assessment.rationale)
                    if ruled is not None:
                        reading = ruled.reading
                        rec["reading_source"] = "rules"
                    else:
                        raw2, dt2 = server.text_json(READ_DEPTH_PROMPT + assessment.rationale, reading_schema)
                        reading = parse_reading(raw2)
                        rec["reading_source"] = "llm"
                    rec["reading"] = reading.model_dump()
                rec["latency_s"] = round(dt1 + dt2, 2)
                rec["latency_pass1_s"] = round(dt1, 2)
                rec["latency_pass2_s"] = round(dt2, 2)
                rec["depth"] = derive_depth(assessment, reading).model_dump()
            except (ValueError, ValidationError, OSError) as e:
                rec["error"] = f"{type(e).__name__}: {str(e)[:300]}"
                rec["raw"] = raw
            store.append(rec)
            new += 1
            if new % 10 == 0:
                log.info("%d assessed this run", new)
    records = list(store)
    summary = summarise(records)
    manifest.finish(new_this_run=new, **summary).write(paths.results / f"vlm_run_{Path(model).stem}.json")
    log.info("assess done: %s", summary)
    return summary


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()[:12]
