"""Structured output the vision-language extractor must produce for every photo.

The schema is the contract between the VLM, the fusion layer and the map. Constrained decoding enforces it;
anything that fails validation is logged and the photo falls back to the offline screen (DINOv3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DepthClass = Literal["dry", "ankle", "knee", "waist", "chest", "above_head", "unknown"]
ReferenceObject = Literal[
    "person", "car", "motorbike", "rickshaw", "bus", "door", "window", "building_ground_floor", "none"
]
WaterLevel = Literal["not_touching", "base", "quarter", "half", "three_quarters", "top", "above"]
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
    reference_object: ReferenceObject = Field(
        "none", description="the object in the water that best shows its depth; 'none' if nothing usable"
    )
    water_level: WaterLevel = Field("not_touching", description="how far up the reference object the water reaches")
    people_visible: int = Field(0, ge=0)
    people_on_roofs_or_elevated: int = Field(0, ge=0)
    people_in_water: int = Field(0, ge=0)
    vehicles_submerged: int = Field(0, ge=0)
    buildings_inundated: bool = False
    water_flow: Flow = "unknown"
    night: bool = False
    visible_text: list[VisibleText] = []
    confidence: float = Field(..., ge=0, le=1)


class DerivedDepth(BaseModel):
    """Computed from (reference_object, water_level) by ``floodline.depth``; never asked of the model."""

    depth_class: DepthClass
    depth_cm_low: float | None = None
    depth_cm_high: float | None = None


class LocatedReport(BaseModel):
    image_id: str
    assessment: PhotoAssessment
    depth: DerivedDepth | None = None
    lat: float | None = None
    lon: float | None = None
    location_tier: LocationTier = "none"
    location_confidence: float = Field(0.0, ge=0, le=1)
    h3_r8: str | None = None
    captured_at: str | None = None


# Prompt used with the VLM. Kept here so the schema and the instructions change together.
EXTRACTION_PROMPT = """You are assessing one photograph for flood emergency triage. Return ONLY a JSON object.

Work in this order:
1. rationale: two short sentences. First, what is in the picture (people, vehicles, buildings, water, and whether
   the view is from the ground or from the air). Second, name the ONE object standing in the water that best shows
   how deep it is, and how far up that object the water reaches.
2. aerial_view: true for drone, aircraft or rooftop views looking down.
3. people_visible: count EVERY person you can see, on dry ground, in boats or in water.
   people_in_water: only those standing or wading in water. people_on_roofs_or_elevated: on roofs, walls, trees.
4. reference_object: the object from your rationale: person, car, motorbike, rickshaw, bus, door, window,
   building_ground_floor, or none. Prefer a person or a car when several are present.
5. water_level: how far up that object the water reaches:
   not_touching (object stands on dry ground), base (just at its bottom), quarter, half, three_quarters,
   top (object fully submerged, top edge at the surface), above (object completely under water).
   For a person: base = ankles, quarter = knees, half = waist, three_quarters = chest, top = head.
   For a car: base = bottom of wheels, quarter = wheel hubs, half = door handles, three_quarters = windows, top = roof.
6. visible_text: only words you can read letter by letter; otherwise an empty list.
7. confidence: 0.9 for a clear ground-level view with a reference object in the water; 0.6 for distant, partial
   or night views; 0.3 for aerial views or when reference_object is none.
The counts, reference_object and water_level must agree with the rationale.
"""
