"""Structured output the vision-language extractor must produce for every photo.

The schema is the contract between the VLM, the fusion layer and the map. Constrained decoding enforces it;
anything that fails validation is logged and the photo falls back to the offline screen (DINOv3).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DepthClass = Literal["dry", "ankle", "knee", "waist", "chest", "above_head", "unknown"]
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
    depth_class: DepthClass = "unknown"
    depth_reference: str | None = Field(None, description="object used to judge depth, e.g. 'car wheels'")
    people_visible: int = Field(0, ge=0)
    people_on_roofs_or_elevated: int = Field(0, ge=0)
    people_in_water: int = Field(0, ge=0)
    vehicles_submerged: int = Field(0, ge=0)
    buildings_inundated: bool = False
    water_flow: Flow = "unknown"
    night: bool = False
    visible_text: list[VisibleText] = []
    confidence: float = Field(..., ge=0, le=1)


class LocatedReport(BaseModel):
    image_id: str
    assessment: PhotoAssessment
    lat: float | None = None
    lon: float | None = None
    location_tier: LocationTier = "none"
    location_confidence: float = Field(0.0, ge=0, le=1)
    h3_r8: str | None = None
    captured_at: str | None = None


# Prompt used with the VLM. Kept here so the schema and the instructions change together.
EXTRACTION_PROMPT = """You are assessing one photograph for flood emergency triage. Return ONLY a JSON object.

Work in this order:
1. rationale: two short sentences. First, what is in the picture (people, vehicles, buildings, water, viewpoint:
   ground level or aerial). Second, which object the water reaches and how far up it (e.g. "water at the car's
   door handles", "person standing on dry pavement beside the water").
2. people_visible: count EVERY person you can see, including people standing on dry ground or in boats.
   people_in_water: only those standing or wading in water. people_on_roofs_or_elevated: on roofs, walls, trees.
3. depth_class, judged from the object named in the rationale, using these heights: adult person 170 cm
   (ankle 10, knee 50, waist 100, chest 140), car wheel 65 cm, car door handle 95 cm, car roof 150 cm,
   motorbike handlebar 110 cm, door 200 cm, ground-floor window sill 100 cm.
   Use "dry" if water is present but the reference object stands on dry ground.
   Use "unknown" ONLY when no person, vehicle, door or window touches the water. Aerial views are "unknown".
4. visible_text: only words you can actually read letter by letter. If unsure, leave the list empty.
5. confidence: 0.9 for a clear ground-level view with a reference object in the water; 0.6 for distant,
   partial or night views; 0.3 for aerials or no reference object.
The counts and depth_class must agree with the rationale.
"""
