"""Floodline: re-rank hexagons with your own weights (a QGIS Processing script).

Add it once: Processing Toolbox > Scripts (the Python icon) > Add Script to Toolbox..., pick this file. It then
appears as Floodline > Re-rank hexagons. Run it on the ``priority_hexes`` layer of a Floodline GeoPackage.

It applies the formula of ``fuse.score`` (src/floodline/stages/fuse.py) to the published components:

    priority = clip(w_exposure * exposure + w_hazard * hazard + w_access * access, 0, 1)
               where the hexagon is wet or cut off, else 0

and writes ``priority_custom`` and ``rank_custom`` (1 = go first). The defaults reproduce the published ranking;
a coordinator who cares most about reachability, say, raises ``w_access`` and sees which hexagons move.
tests/test_qgis.py checks that this file and fuse.py give the same priority.

Whether a hexagon is wet does not depend on the weights, so it is read from what fuse.py published: hazard > 0,
satellite_water_fraction > 0, or a published priority above zero. The last two matter because hazard is published
to three decimals: a sliver of mapped water (0.0005) shows hazard 0.000, though fuse.py, unrounded, counted it.
"""

from __future__ import annotations

DEFAULT_WEIGHTS = {"exposure": 0.45, "hazard": 0.30, "access": 0.25}  # fuse.Weights
REQUIRED_FIELDS = ("exposure", "hazard", "access", "cut_off")
WATER_FIELD = "satellite_water_fraction"  # optional; see the note on wet hexagons above
PUBLISHED_FIELD = "priority"  # optional, as above


def _number(value) -> float:
    """QGIS hands NULL as None (or a NULL QVariant, which is falsy); an absent component counts as zero."""
    try:
        return float(value) if value else 0.0
    except (TypeError, ValueError):
        return 0.0


def fused_priority(
    exposure,
    hazard,
    access,
    cut_off,
    w_exposure: float,
    w_hazard: float,
    w_access: float,
    water=None,
    published=None,
) -> float:
    hazard_v = _number(hazard)
    if not (hazard_v > 0 or _number(water) > 0 or _number(published) > 0 or bool(cut_off)):
        return 0.0
    value = w_exposure * _number(exposure) + w_hazard * hazard_v + w_access * _number(access)
    return round(min(max(value, 0.0), 1.0), 3)


def ranks(priorities: list[float]) -> list[int]:
    """Rank 1 for the highest priority; equal priorities keep the input order."""
    order = sorted(range(len(priorities)), key=lambda i: -priorities[i])
    out = [0] * len(priorities)
    for rank, i in enumerate(order, start=1):
        out[i] = rank
    return out


try:
    from qgis.core import (
        QgsFeature,
        QgsFeatureSink,
        QgsField,
        QgsFields,
        QgsProcessing,
        QgsProcessingAlgorithm,
        QgsProcessingException,
        QgsProcessingParameterFeatureSink,
        QgsProcessingParameterFeatureSource,
        QgsProcessingParameterNumber,
    )
    from qgis.PyQt.QtCore import QVariant
except ImportError:  # imported outside QGIS, by the test that checks the formula against fuse.py
    QgsProcessingAlgorithm = object


class RerankHexagons(QgsProcessingAlgorithm):
    INPUT = "INPUT"
    W_EXPOSURE = "W_EXPOSURE"
    W_HAZARD = "W_HAZARD"
    W_ACCESS = "W_ACCESS"
    TOP_N = "TOP_N"
    OUTPUT = "OUTPUT"

    def name(self) -> str:
        return "rerank_hexagons"

    def displayName(self) -> str:
        return "Re-rank hexagons"

    def group(self) -> str:
        return "Floodline"

    def groupId(self) -> str:
        return "floodline"

    def shortHelpString(self) -> str:
        return (
            "Re-ranks Floodline hexagons with your own weights for exposure (buildings and people affected), hazard "
            "(water) and access (cut off, or the detour the flood forces). The defaults 0.45 / 0.30 / 0.25 "
            "reproduce the published ranking. Hexagons that are neither wet nor cut off stay at 0, whatever the "
            "weights. Adds priority_custom and rank_custom (1 = go first); a top-N above 0 keeps only those."
        )

    def createInstance(self):
        return RerankHexagons()

    def initAlgorithm(self, config=None):
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                self.INPUT, "Floodline hexagons (priority_hexes)", [QgsProcessing.TypeVectorPolygon]
            )
        )
        for key, label, default in (
            (self.W_EXPOSURE, "Weight: exposure", DEFAULT_WEIGHTS["exposure"]),
            (self.W_HAZARD, "Weight: hazard", DEFAULT_WEIGHTS["hazard"]),
            (self.W_ACCESS, "Weight: access", DEFAULT_WEIGHTS["access"]),
        ):
            self.addParameter(
                QgsProcessingParameterNumber(
                    key, label, QgsProcessingParameterNumber.Double, defaultValue=default, minValue=0.0, maxValue=1.0
                )
            )
        self.addParameter(
            QgsProcessingParameterNumber(
                self.TOP_N, "Keep only the top N (0 keeps all)", QgsProcessingParameterNumber.Integer, 0, False, 0
            )
        )
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Re-ranked hexagons"))

    def processAlgorithm(self, parameters, context, feedback):
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException("No input layer")
        missing = [f for f in REQUIRED_FIELDS if source.fields().indexOf(f) < 0]
        if missing:
            raise QgsProcessingException(
                f"The input lacks {', '.join(missing)}; use the priority_hexes layer of a Floodline GeoPackage"
            )
        weights = [
            self.parameterAsDouble(parameters, k, context) for k in (self.W_EXPOSURE, self.W_HAZARD, self.W_ACCESS)
        ]
        if sum(weights) <= 0:
            raise QgsProcessingException("At least one weight must be above zero")
        if abs(sum(weights) - 1.0) > 1e-6:
            feedback.pushInfo(f"Weights sum to {sum(weights):.2f}; priority is clipped to [0, 1], as in fuse.py.")
        top_n = self.parameterAsInt(parameters, self.TOP_N, context)

        features = list(source.getFeatures())
        names = set(source.fields().names())
        water, published = (field if field in names else None for field in (WATER_FIELD, PUBLISHED_FIELD))
        priorities = [
            fused_priority(
                f["exposure"],
                f["hazard"],
                f["access"],
                f["cut_off"],
                *weights,
                water=f[water] if water else None,
                published=f[published] if published else None,
            )
            for f in features
        ]
        rank_of = ranks(priorities)

        fields = QgsFields(source.fields())
        fields.append(QgsField("priority_custom", QVariant.Double))
        fields.append(QgsField("rank_custom", QVariant.Int))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, source.wkbType(), source.sourceCrs()
        )
        order = sorted(range(len(features)), key=lambda i: rank_of[i])
        if top_n > 0:
            order = order[:top_n]
        for done, i in enumerate(order):
            if feedback.isCanceled():
                break
            out = QgsFeature(fields)
            out.setGeometry(features[i].geometry())
            out.setAttributes([*features[i].attributes(), priorities[i], rank_of[i]])
            sink.addFeature(out, QgsFeatureSink.FastInsert)
            feedback.setProgress(int(100 * (done + 1) / max(len(order), 1)))
        return {self.OUTPUT: dest_id}
