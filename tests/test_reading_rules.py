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


def test_person_wins_over_vehicle_in_same_sentence():
    r = read_from_text("A man stands in the flooded street beside submerged vehicles; the water reaches his knees.")
    assert r is not None and (r.reading.reference_object, r.reading.water_level) == ("person", "quarter")


def test_non_reference_objects_short_circuit_to_none():
    r = read_from_text("A flooded river with water around a lamppost and some trees.")
    assert r is not None and r.reading.reference_object == "none"
    r2 = read_from_text("The water reaches the base of a stone pillar, covering it up to about waist height.")
    assert r2 is not None and r2.reading.reference_object == "none"


def test_nothing_matched_returns_none():
    assert read_from_text("Aerial view of fields covered in brown water.") is None


def test_explicit_contact_beats_a_dry_ground_phrase():
    from floodline.reading_rules import read_from_text

    text = (
        "A man stands on dry ground looking at a flooded street with submerged vehicles and buildings in the "
        "background; the water reaches his ankles."
    )
    r = read_from_text(text)
    assert r is not None and r.reading.reference_object == "person" and r.reading.water_level == "base"
    dry = read_from_text("A woman is standing on dry ground at the edge of the water, her feet visible.")
    assert dry is not None and dry.reading.water_level == "not_touching"
