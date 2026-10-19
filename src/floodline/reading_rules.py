"""Deterministic reading of (reference object, water level) from a pass-1 description.

The description names a body part or car part ("water up to their knees", "at the door handles"). Mapping those
phrases to a level is a lookup, not a judgement, so it is done here in code and covered by tests. The LLM text pass
is only the fallback for descriptions these rules cannot parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from floodline.schema import DepthReading, ReferenceObject, WaterLevel

# Reference nouns, most specific first. The first family found in the description wins.
_OBJECT_PATTERNS: tuple[tuple[ReferenceObject, str], ...] = (
    # Person first: body-part phrases are unambiguous, so a person mention wins over a vehicle in the same sentence.
    (
        "person",
        r"\bperson\b|\bpeople\b|\bman\b|\bwoman\b|\bmen\b|\bwomen\b|\bchild(ren)?\b|\bboy\b|\bgirl\b|\bpedestrians?\b|\bresidents?\b|\brescuers?\b",
    ),
    ("rickshaw", r"\brickshaws?\b|\btuk-?tuks?\b"),
    ("bus", r"\bbus(es)?\b|\btrucks?\b|\blorr(y|ies)\b"),
    ("motorbike", r"\bmotor ?(bike|cycle)s?\b|\bscooters?\b|\bbikes?\b"),
    ("car", r"\bcars?\b|\bvehicles?\b|\bsuvs?\b|\bvans?\b|\bjeeps?\b|\btaxis?\b"),
    ("door", r"\bdoors?\b|\bdoorways?\b|\bdoorsteps?\b|\bthreshold\b"),
    ("window", r"\bwindows?\b|\bwindow ?sills?\b"),
    ("building_ground_floor", r"\bground floor\b|\bfirst floor\b|\bbuildings?\b|\bhouses?\b|\bshops?\b|\bwalls?\b"),
)

# Level phrases per object family. Order inside each tuple: most specific pattern first.
_LEVELS: dict[str, tuple[tuple[WaterLevel, str], ...]] = {
    "person": (
        (
            "above",
            r"over (their|his|her) heads?|above (their|his|her) heads?|submerg(ed|ing) (the )?(person|people|man|woman)",
        ),
        ("top", r"\bneck|\bchin|\bshoulders?|\bhead\b"),
        ("three_quarters", r"\bchest|\barmpits?|\bbreast"),
        ("half", r"\bwaist|\bhips?|\bbelly|\bstomach|\bthighs?"),
        ("quarter", r"\bknees?\b|\bcalf|\bcalves|\bshins?"),
        ("base", r"\bankles?|\bfeet\b|\bfoot\b|\bboots?|\bshoes?"),
    ),
    "car": (
        ("above", r"(completely|fully|entirely) (submerged|under ?water|covered)|over the roof|above the roof"),
        ("top", r"\broof(s|line)?\b|\broof level"),
        ("three_quarters", r"\bwindows?\b|\bwindshield|\bwindscreen|\bbonnet|\bhood\b|\bmirrors?"),
        ("half", r"door handles?|\bdoors?\b|\bbumpers?\b|\bgrille?\b|\bheadlights?"),
        ("quarter", r"wheel (hubs?|arch(es)?|wells?)|\bhubcaps?|\baxles?|half(way)? up the (wheels?|tyres?|tires?)"),
        ("base", r"\bwheels?\b|\btyres?\b|\btires?\b"),
    ),
    "door": (
        ("above", r"\bfirst floor|\bupper floor|\broof"),
        ("top", r"top of the door|door ?frame|lintel"),
        ("half", r"door handles?|\bknobs?|half(way)? up the door"),
        ("base", r"\bdoorsteps?|\bthreshold|bottom of the door|base of the door"),
    ),
    "window": (
        ("above", r"above the windows?|over the windows?|\bfirst floor|\broof"),
        ("top", r"top of the windows?"),
        ("half", r"half(way)? up the windows?|middle of the windows?"),
        ("base", r"window ?sills?|bottom of the windows?|base of the windows?"),
    ),
    "building_ground_floor": (
        ("above", r"\broof(s|tops?)?\b|\bsecond floor|\bupper floors?|\bfirst floor"),
        ("three_quarters", r"top of the (door|windows?)|lintel"),
        ("half", r"window ?sills?|door handles?|half(way)? up the (ground floor|wall|building)"),
        ("base", r"\bdoorsteps?|\bground level|\bbase of the (building|wall|house)|\bfoundations?"),
    ),
}
_LEVELS["motorbike"] = (
    ("above", r"(completely|fully) (submerged|under ?water)"),
    ("top", r"\bseat|\bhandlebars?|\bmirrors?"),
    ("half", r"\bengine|\btank\b|\bfootpegs?"),
    ("quarter", r"wheel (hubs?|axles?)|half(way)? up the wheels?"),
    ("base", r"\bwheels?\b|\btyres?\b|\btires?\b"),
)
_LEVELS["rickshaw"] = _LEVELS["car"]
_LEVELS["bus"] = _LEVELS["car"]

_NON_REFERENCE = re.compile(
    r"(pillars?|lamp ?posts?|poles?|trees?|trunks?|bushes|shrubs?|signs?|fences?|railings?|benches|bollards?)",
    re.I,
)
_DRY = re.compile(
    r"\bdry (ground|pavement|land|road|street)\b|standing on dry|not (in|touching) (the )?water|beside the water|next to the water|edge of the water",
    re.I,
)
_NO_REFERENCE = re.compile(
    r"\bno (visible )?(people|person|vehicles?|cars?|objects?)\b.*\b(in|standing in) the water", re.I
)


@dataclass(frozen=True)
class RuleReading:
    reading: DepthReading
    matched_object: str
    matched_level: str


def read_from_text(description: str) -> RuleReading | None:
    """Return a reading when the description names a reference object and a level for it.

    Returns a ``none`` reading when the only thing in the water is a non-reference object (pillar, tree, sign),
    so the LLM fallback is never asked to invent a reference. Returns ``None`` only when nothing matched at all.
    """
    text = description.lower()
    for obj, pat in _OBJECT_PATTERNS:
        m = re.search(pat, text)
        if not m:
            continue
        # Look for the level phrase near the object mention first, then anywhere in the text.
        window = text[max(0, m.start() - 160) : m.end() + 200]
        for scope in (window, text):
            for level, lpat in _LEVELS[obj]:
                lm = re.search(lpat, scope)
                if lm:
                    if _DRY.search(scope) and level == "base":
                        level = "not_touching"
                    basis = scope[max(0, lm.start() - 40) : lm.end() + 20].strip()
                    return RuleReading(
                        DepthReading(reference_object=obj, water_level=level, basis=basis[:160]),
                        matched_object=m.group(0),
                        matched_level=lm.group(0),
                    )
        if _DRY.search(window):
            return RuleReading(
                DepthReading(reference_object=obj, water_level="not_touching", basis=_DRY.search(window).group(0)),
                matched_object=m.group(0),
                matched_level="dry",
            )
    nr = _NON_REFERENCE.search(text)
    if nr:
        return RuleReading(
            DepthReading(
                reference_object="none", water_level="not_touching", basis=f"only a {nr.group(0)} in the water"
            ),
            matched_object=nr.group(0),
            matched_level="none",
        )
    return None
