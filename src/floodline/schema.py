"""Structured output the vision-language extractor must produce for every photo.

The schema is the contract between the VLM, the fusion layer and the map. Constrained decoding enforces it;
anything that fails validation is logged and the photo falls back to the offline screen (DINOv3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DepthClass = Literal["dry", "ankle", "knee", "waist", "chest", "above_head", "unknown"]
# Enum order matters: grammar-constrained greedy decoding tends to take the first alternative when unsure, so the
# informative values come first and the "nothing usable" values last.
ReferenceObject = Literal[
    "person", "car", "motorbike", "rickshaw", "bus", "door", "window", "building_ground_floor", "none"
]
WaterLevel = Literal["base", "quarter", "half", "three_quarters", "top", "above", "not_touching"]
Flow = Literal["still", "slow", "fast", "unknown"]
LocationTier = Literal["gps", "visual_text", "visual_landmark", "report_text", "district", "none"]


class VisibleText(BaseModel):
    text: str
    kind: Literal["shop_sign", "road_sign", "banner", "vehicle", "other"] = "other"


class PhotoAssessment(BaseModel):
    # rationale comes first on purpose: with grammar-constrained decoding the model fills fields in schema
    # order, so it describes the scene before committing to counts and classes.
    rationale: str = Field(
        ...,
        max_length=300,
        description="two short sentences: what is visible, what the water reaches on which object",
    )
    is_flood_scene: bool
    aerial_view: bool = False
    people_visible: int = Field(0, ge=0)
    people_on_roofs_or_elevated: int = Field(0, ge=0)
    people_in_water: int = Field(0, ge=0)
    vehicles_submerged: int = Field(0, ge=0)
    buildings_inundated: bool = False
    water_flow: Flow = "unknown"
    night: bool = False
    visible_text: list[VisibleText] = []
    confidence: float = Field(..., ge=0, le=1)


class DepthReading(BaseModel):
    """Pass 2: read from the pass-1 description only, no image. Both fields are required so the grammar cannot
    fall back to a default; ``none`` / ``not_touching`` must be chosen deliberately."""

    reference_object: ReferenceObject = Field(..., description="the object standing in the water that shows its depth")
    water_level: WaterLevel = Field(..., description="how far up that object the water reaches")
    basis: str = Field(..., max_length=160, description="the phrase from the description this is based on")


class DerivedDepth(BaseModel):
    """Computed from (reference_object, water_level) by ``floodline.depth``; never asked of the model."""

    depth_class: DepthClass
    depth_cm_low: float | None = None
    depth_cm_high: float | None = None


class LocatedReport(BaseModel):
    image_id: str
    assessment: PhotoAssessment
    reading: DepthReading | None = None
    depth: DerivedDepth | None = None
    lat: float | None = None
    lon: float | None = None
    location_tier: LocationTier = "none"
    location_confidence: float = Field(0.0, ge=0, le=1)
    h3_r8: str | None = None
    captured_at: str | None = None


# Prompt used with the VLM. Kept here so the schema and the instructions change together.
DESCRIBE_PROMPT = """You are assessing one photograph for flood emergency triage. Return ONLY a JSON object.

1. rationale: two short sentences. First, what is in the picture (people, vehicles, buildings, water, and whether
   the view is from the ground or from the air). Second, name the ONE object standing in the water that best shows
   how deep it is, and say how far up that object the water reaches (for a person: ankles, knees, waist, chest,
   head; for a car: wheels, hubs, door handles, windows, roof; for a building: doorstep, door handle, window sill,
   top of the door, first floor).
2. aerial_view: true for drone, aircraft or rooftop views looking down.
3. people_visible: count EVERY person you can see, on dry ground, in boats or in water.
   people_in_water: only those standing or wading in water. people_on_roofs_or_elevated: on roofs, walls, trees.
4. visible_text: only words you can read letter by letter; otherwise an empty list.
5. confidence: 0.9 for a clear ground-level view with an object standing in the water; 0.6 for distant, partial
   or night views; 0.3 for aerial views or when nothing stands in the water.
"""

READ_DEPTH_PROMPT = """Below is a description of a flood photograph. Using ONLY the description, answer as JSON.

reference_object: the object the description says is standing in the water: person, car, motorbike, rickshaw,
bus, door, window, building_ground_floor. Use none only if the description names no object in the water.
water_level: how far up that object the water reaches, as the description states it:
  person:   ankles -> base, knees -> quarter, waist -> half, chest -> three_quarters, head -> top, over head -> above
  car:      bottom of wheels -> base, wheel hubs -> quarter, door handles -> half, windows -> three_quarters,
            roof -> top, car fully under water -> above
  door/building: doorstep -> base, door handle -> half, top of door -> top, first floor or roof -> above
  Use not_touching only if the description says the object stands on dry ground.
basis: copy the phrase from the description that states the water level.

Description:
"""

# Kept for callers that still import the old name.
EXTRACTION_PROMPT = DESCRIBE_PROMPT
