import pytest

from floodline.reading_rules import read_from_text


@pytest.mark.parametrize(
    "text,obj,level",
    [
        ("Two people stand in floodwater. The water is up to their knees.", "person", "quarter"),
        ("A man wades through the street with water at his waist.", "person", "half"),
        ("Water reaches the chest of a rescuer carrying a child.", "person", "three_quarters"),
        ("A car sits in the flood with water at the door handles.", "car", "half"),
        ("Several vehicles are submerged up to their windows.", "car", "three_quarters"),
        ("Water covers the wheels of a parked car.", "car", "base"),
        ("The water reaches the window sills of the houses.", "window", "base"),
        ("Water is at the doorstep of the shop.", "door", "base"),
        ("Flooding reaches the rooftops of the houses.", "building_ground_floor", "above"),
    ],
)
def test_rules_read_object_and_level(text, obj, level):
    r = read_from_text(text)
    assert r is not None
    assert (r.reading.reference_object, r.reading.water_level) == (obj, level)


def test_dry_ground_is_not_touching():
    r = read_from_text("A man stands on dry pavement beside the water, looking at the flooded street.")
    assert r is not None and r.reading.water_level == "not_touching"


def test_no_reference_returns_none():
    assert read_from_text("A flooded river with water around a lamppost and some trees.") is None
    assert read_from_text("Aerial view of fields covered in brown water.") is None
