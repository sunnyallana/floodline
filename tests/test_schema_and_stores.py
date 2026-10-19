import json

import pytest
from pydantic import ValidationError

from floodline.resume import CsvStore, JsonlStore
from floodline.schema import DepthReading, PhotoAssessment
from floodline.stages.assess import derive_depth, parse_assessment, summarise


def test_schema_puts_rationale_first():
    props = list(PhotoAssessment.model_json_schema()["properties"])
    assert props[0] == "rationale"


def test_parse_assessment_tolerates_wrapping_text():
    raw = 'Sure: {"rationale": "a person in water to the knees", "is_flood_scene": true, "confidence": 0.9}'
    a = parse_assessment(raw)
    assert a.people_visible == 0 and not a.aerial_view
    reading = DepthReading(reference_object="person", water_level="quarter", basis="to the knees")
    assert derive_depth(a, reading).depth_class == "knee"


def test_parse_assessment_rejects_bad_values():
    with pytest.raises(ValidationError):
        parse_assessment('{"rationale": "x", "is_flood_scene": true, "confidence": 1.5}')
    with pytest.raises(ValueError):
        parse_assessment("no json here")


def test_summarise_counts_failures():
    recs = [{"assessment": {}, "depth": {"depth_class": "knee"}, "latency_s": 10}, {"error": "boom", "latency_s": 12}]
    s = summarise(recs)
    assert s["images"] == 2 and s["valid_json"] == 1 and s["failure_rate"] == 0.5
    assert s["depth_class_counts"] == {"knee": 1}


def test_jsonl_store_roundtrip(tmp_path):
    store = JsonlStore(tmp_path / "x.jsonl")
    assert store.done() == set()
    store.append({"image_id": "a", "v": 1})
    store.append({"image_id": "b", "v": 2})
    assert store.done() == {"a", "b"}
    assert [r["v"] for r in store] == [1, 2]


def test_csv_store_unions_headers_and_is_atomic(tmp_path):
    store = CsvStore(tmp_path / "s.csv")
    store.extend([{"image_id": "a", "water_iou": 0.9}])
    store.extend([{"image_id": "b", "person_iou": 0.5}])
    rows = store.rows()
    assert [r["image_id"] for r in rows] == ["a", "b"]
    assert rows[1]["water_iou"] == "" and rows[0]["person_iou"] == ""
    assert not (tmp_path / "s.tmp").exists()
    assert store.done() == {"a", "b"}


def test_manifest_serialises(tmp_path):
    from floodline.runs import RunManifest

    m = RunManifest("unit", params={"k": 1}).finish(n=2)
    out = m.write(tmp_path / "m.json")
    data = json.loads(out.read_text())
    assert data["stage"] == "unit" and data["metrics"] == {"n": 2} and data["seconds"] is not None
    assert "_t0" not in data


@pytest.mark.parametrize(
    "obj,level,expected",
    [
        ("person", "not_touching", "dry"),
        ("person", "base", "ankle"),
        ("person", "quarter", "knee"),
        ("person", "half", "waist"),
        ("person", "three_quarters", "chest"),
        ("person", "above", "above_head"),
        ("car", "half", "waist"),
        ("door", "half", "chest"),
        ("building_ground_floor", "half", "chest"),
        ("building_ground_floor", "three_quarters", "above_head"),
        ("none", "half", "unknown"),
    ],
)
def test_derive_depth_from_reference_and_level(obj, level, expected):
    a = PhotoAssessment(rationale="r", is_flood_scene=True, confidence=0.9)
    reading = DepthReading(reference_object=obj, water_level=level, basis="r")
    assert derive_depth(a, reading).depth_class == expected


def test_aerial_views_and_missing_readings_are_unknown():
    a = PhotoAssessment(rationale="r", is_flood_scene=True, aerial_view=True, confidence=0.3)
    reading = DepthReading(reference_object="car", water_level="top", basis="r")
    assert derive_depth(a, reading).depth_class == "unknown"
    assert (
        derive_depth(PhotoAssessment(rationale="r", is_flood_scene=True, confidence=0.9), None).depth_class == "unknown"
    )


def test_reading_fields_are_required():
    with pytest.raises(ValidationError):
        DepthReading(basis="x")  # type: ignore[call-arg]
