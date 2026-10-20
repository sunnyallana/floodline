"""How much does the ranking depend on the hand-set parts of the priority score?

The weights (exposure 0.45, hazard 0.30, access 0.25) and the scale constants were set by judgement, not fitted:
there is no labelled "where responders should have gone" data to fit them to. This stage reports how stable
the ranking is when each of them moves:

- the three weights, each scaled by 0.5, 0.75, 1, 1.25 or 1.5 and renormalised to sum to 1 (every combination);
- the exposure scale, the per-person weight and the delay saturation, each at 0.5x and 2x.

For each variant: overlap of the top-k hexagons with the default ranking (|A n B| / k) and Spearman rank
correlation over hexagons with priority > 0 under either. When validation towns are given, also the share of
graded damage captured by the top 10 % under each weight variant, so stability and accuracy are read together.
"""

from __future__ import annotations

import itertools
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest
from floodline.stages.fuse import Weights, score

log = get_logger(__name__)

FACTORS: tuple[float, ...] = (0.5, 0.75, 1.0, 1.25, 1.5)
SCALE_FACTORS: tuple[float, ...] = (0.5, 2.0)
TOP_K: tuple[int, ...] = (5, 10, 20)


def weight_variants(base: Weights | None = None) -> list[tuple[str, Weights]]:
    base = base or Weights()
    out = []
    for fe, fh, fa in itertools.product(FACTORS, repeat=3):
        e, hz, a = base.exposure * fe, base.hazard * fh, base.access * fa
        total = e + hz + a
        out.append((f"w{fe:g}/{fh:g}/{fa:g}", replace(base, exposure=e / total, hazard=hz / total, access=a / total)))
    for field in ("exposure_scale", "person_weight", "delay_saturation_min"):
        for f in SCALE_FACTORS:
            out.append((f"{field}x{f:g}", replace(base, **{field: getattr(base, field) * f})))
    return out


def compare(base: pd.Series, other: pd.Series, ks=TOP_K) -> dict[str, float]:
    """Top-k overlap and Spearman between two priority series indexed by hexagon."""
    out: dict[str, float] = {}
    for k in ks:
        top_a = set(base.sort_values(ascending=False).head(k).index)
        top_b = set(other.sort_values(ascending=False).head(k).index)
        out[f"top{k}_overlap"] = round(len(top_a & top_b) / k, 3)
    live = (base > 0) | (other > 0)
    if live.sum() > 2:
        out["spearman_live"] = round(float(base[live].rank().corr(other[live].rank())), 3)
    return out


def _priority(hexes: pd.DataFrame, w: Weights) -> pd.Series:
    return score(hexes, w).set_index("h3").priority


def stability(hexes: pd.DataFrame) -> pd.DataFrame:
    base = _priority(hexes, Weights())
    rows = []
    for name, w in weight_variants():
        rows.append({"variant": name, **compare(base, _priority(hexes, w))})
    return pd.DataFrame(rows)


def damage_capture(hexes: pd.DataFrame, damage: pd.Series, frac: float = 0.10) -> pd.DataFrame:
    """Share of graded damage in the top ``frac`` of hexagons under each weight variant."""
    from floodline.stages.validate import capture

    rows = []
    for name, w in weight_variants():
        p = _priority(hexes, w).reindex(damage.index).fillna(0)
        rows.append({"variant": name, **capture(p, damage, (frac,))})
    return pd.DataFrame(rows)


def _summary(df: pd.DataFrame) -> dict[str, Any]:
    cols = [c for c in df.columns if c != "variant"]
    return {c: {"min": float(df[c].min()), "median": float(df[c].median())} for c in cols}


def run(paths: Paths, hexes_csvs: list[Path], validation_dir: Path | None = None) -> dict[str, Any]:
    manifest = RunManifest("sensitivity", params={"hexes": [str(p) for p in hexes_csvs], "factors": FACTORS})
    summary: dict[str, Any] = {"variants": len(weight_variants())}
    for path in hexes_csvs:
        hexes = pd.read_csv(path)
        table = stability(hexes)
        table.to_csv(paths.results / f"sensitivity_{path.stem}.csv", index=False)
        summary[path.stem] = _summary(table)
    if validation_dir is not None:
        for ranking in sorted(validation_dir.glob("ranking_*.csv")):
            key = ranking.stem.removeprefix("ranking_")
            hexes_csv = validation_dir / f"hexes_{key}.csv"
            if not hexes_csv.exists():
                continue
            hexes = pd.read_csv(hexes_csv)
            damage = pd.read_csv(ranking).set_index("h3").severe
            table = damage_capture(hexes, damage)
            table.to_csv(paths.results / f"sensitivity_damage_{key}.csv", index=False)
            default = table.loc[table.variant == "w1/1/1", "top10"].iloc[0]
            summary[f"damage_{key}"] = {"default_top10": float(default), **_summary(table)}
    manifest.finish(**summary).write(paths.results / "sensitivity_run.json")
    log.info("sensitivity: %s", {k: v for k, v in summary.items() if k != "variants"})
    return summary
