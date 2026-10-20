"""Multi-event validation: one protocol, every flood activation for which EMS published damage grading.

For each event and each graded town with at least ``MIN_SEVERE_BUILDINGS`` damaged or destroyed buildings:
satellite (the grading product's own flood polygons) -> reach (OSM from a pinned extract) -> exposure (the
per-hexagon maximum of OSM and Microsoft ML footprints, so towns where OSM is thin are not penalised) ->
population (GHSL, the humanitarian "people in the flood" layer) -> fuse. The road rule is checked on every
graded town with analysed roads.

Scores compared, all on the same hexagons:

- ``priority``: Floodline's gated score;
- ``people_in_flood``: GHSL population inside the flood polygons, the practitioner baseline;
- ``buildings_in_flood``: building footprints inside the flood;
- ``satellite_water_fraction``: flood extent alone.

``summarise`` pools every ranked town and puts a spatial block bootstrap around each number: hexagons are
resampled in blocks of their H3 resolution-6 parent (about 36 km2), within each town, because neighbouring
hexagons share damage and a per-hexagon bootstrap would understate the uncertainty.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from floodline.config import Paths
from floodline.log import get_logger
from floodline.runs import RunManifest
from floodline.stages.validate import (
    MIN_SEVERE_BUILDINGS,
    SEVERE,
    _key,
    _layer,
    _town,
    capture,
    damage_per_cell,
    road_rule,
)

log = get_logger(__name__)

SCORES: tuple[str, ...] = (
    "priority", "people_in_flood", "buildings_in_flood", "satellite_water_fraction", "priority_people",
)  # fmt: skip
BASELINES: tuple[str, ...] = ("people_in_flood", "buildings_in_flood", "satellite_water_fraction")
BLOCK_RESOLUTION = 6
BOOTSTRAP = 1000
TOP = 0.10


def _read_json(path: Path) -> dict[str, Any] | None:
    import json

    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("metrics", data)


FLOOD_SOURCES: tuple[str, ...] = ("ems", "sentinel1", "sentinel1_otsu")


def _set(flood_source: str, buildings_source: str = "max") -> str:
    """Results folder: analyst maps under ``benchmark/``, radar under ``benchmark_s1*``; a building source other
    than the default ``max`` adds a suffix, so every ablation run keeps its own folder."""
    base = {"ems": "benchmark", "sentinel1": "benchmark_s1", "sentinel1_otsu": "benchmark_s1otsu"}[flood_source]
    return base if buildings_source == "max" else f"{base}_{buildings_source}"


def _event_paths(paths: Paths, event: str, flood_source: str = "ems", buildings_source: str = "max") -> Paths:
    return Paths(root=paths.root, namespace=f"{_set(flood_source, buildings_source)}/{event}").ensure()


def rank_town(epaths: Paths, d: Path, key: str, buildings, source, buildings_source: str) -> pd.DataFrame:
    """Every layer for one graded town, merged with its damage counts per hexagon."""
    from floodline.population import population_per_hex
    from floodline.stages import exposure, fuse, reach, satellite

    sat_csv = epaths.results / f"satellite_{key}.csv"
    layer = satellite.run(epaths, d, out_name=sat_csv.name, resolution=9)
    reach_csv = epaths.results / f"reach_{key}.csv"
    reach.run(epaths, layer.h3.tolist(), d, out_stem=f"reach_{key}", source=source)
    exp_csv = epaths.results / f"exposure_{key}.csv"
    exposure.run(epaths, sat_csv, d, reach_csv, out_name=exp_csv.name, source=source, buildings_source=buildings_source)
    flood, aoi, _, _ = satellite.load_ems(d)
    pop = population_per_hex(flood, aoi, layer.h3.tolist(), epaths.root / "data" / "external" / "ghsl")
    pop_csv = epaths.results / f"population_{key}.csv"
    pop.to_csv(pop_csv, index=False)
    hexes = fuse.run(epaths, [sat_csv, reach_csv, exp_csv, pop_csv], out_stem=f"hexes_{key}")
    dmg = damage_per_cell(buildings, resolution=9)
    merged = hexes.merge(dmg, on="h3", how="left").fillna({"severe": 0, "affected": 0})
    cols = ["h3", *SCORES, "cut_off", "buildings_affected", "people_total", "severe", "affected"]
    merged = merged[[c for c in cols if c in merged]]
    merged.to_csv(epaths.results / f"ranking_{key}.csv", index=False)
    return merged


def run_event(
    paths: Paths,
    event: str,
    gra_root: Path,
    osm_pbfs: list[Path],
    buildings_source: str = "max",
    only: set[str] | None = None,
    flood_source: str = "ems",
) -> dict[str, Any]:
    """``flood_source="sentinel1"`` replaces each town's EMS flood polygons with a Sentinel-1 water mask from the
    scene nearest the EMS post-event image (the ablation: what is lost without analysts' maps)."""
    from floodline import osm
    from floodline.stages import satellite, sentinel1

    if flood_source not in FLOOD_SOURCES:
        raise ValueError(f"flood_source must be one of {FLOOD_SOURCES}")
    epaths = _event_paths(paths, event, flood_source, buildings_source)
    source = osm.OsmSource(osm_pbfs, paths.data / "external" / "osm" / "extracts")
    towns: dict[str, Any] = {}
    roads = []
    for d in sorted(p for p in gra_root.iterdir() if p.is_dir() and p.name.endswith("_vector")):
        key, town = _key(d), _town(d)
        if only and key not in only:
            continue
        info: dict[str, Any] = {"town": town}
        lines = _layer(d, "transportationL")
        built = _layer(d, "builtUpP")
        try:
            if flood_source.startswith("sentinel1"):
                when = sentinel1.post_event_date(d)
                if when is None:
                    raise FileNotFoundError(f"no post-event image date in {d.name}")
                method = "otsu" if flood_source.endswith("otsu") else "change"
                d, radar = sentinel1.write_radar_package(d, paths.data / "derived" / "s1" / event, when, method)
                info["radar"] = {**radar, "ems_image_date": when.date().isoformat()}
            flood, _, _, _ = satellite.load_ems(d)
        except (FileNotFoundError, RuntimeError) as exc:
            towns[key] = {**info, "error": f"{type(exc).__name__}: {exc}"[:300]}
            continue
        if lines is not None and len(lines) and "damage_gra" in lines:
            import geopandas as gpd

            crs = lines.to_crs("EPSG:4326").estimate_utm_crs()
            flood_m = gpd.GeoSeries([flood], crs="EPSG:4326").to_crs(crs).iloc[0]
            roads.append(road_rule(lines.to_crs(crs), flood_m).assign(event=event, aoi=key, town=town))
        severe = int(built.damage_gra.isin(SEVERE).sum()) if built is not None and "damage_gra" in built else 0
        info["severe_buildings"] = severe
        if severe >= MIN_SEVERE_BUILDINGS:
            try:
                ranked = rank_town(epaths, d, key, built, source, buildings_source)
                info["ranked_hexes"] = len(ranked)
                info["severe_in_mapped_hexes"] = int(ranked.severe.sum())
            except Exception as exc:  # one town's data problem must not discard the event
                log.error("%s %s (%s) failed: %s", event, key, town, exc)
                info["error"] = f"{type(exc).__name__}: {exc}"[:300]
        towns[key] = info
    table = pd.concat(roads, ignore_index=True) if roads else pd.DataFrame()
    previous = _read_json(epaths.results / "event_run.json")
    if only and previous:
        # A rerun of some towns keeps the others' results.
        towns = {**previous.get("towns", {}), **towns}
        old_roads = epaths.results / "roads.csv"
        if old_roads.exists() and old_roads.stat().st_size > 1:
            kept = pd.read_csv(old_roads)
            kept = kept[~kept.aoi.isin(only)] if "aoi" in kept else kept
            table = pd.concat([kept, table], ignore_index=True)
    table.to_csv(epaths.results / "roads.csv", index=False)
    summary = {"event": event, "flood_source": flood_source, "towns": towns, "road_rows": len(table)}
    RunManifest("benchmark_event", params={"event": event, "osm": [str(p) for p in osm_pbfs],
                                           "buildings": buildings_source}).finish(**summary).write(
        epaths.results / "event_run.json"
    )  # fmt: skip
    log.info("benchmark %s: %s", event, {k: v.get("ranked_hexes") for k, v in towns.items()})
    return summary


# ---------------------------------------------------------------- pooling and uncertainty


COMPONENTS: tuple[str, ...] = (
    "exposure", "hazard", "access", "cut_off", "buildings_affected", "people_total", "people_in_flood",
    "satellite_water_fraction", "buildings_in_flood", "priority",
)  # fmt: skip
PEOPLE_SCALE = 100.0  # people at which people-exposure reaches 1 - 1/e (about 20 households)


def load_rankings(paths: Paths, flood_source: str = "ems", buildings_source: str = "max") -> pd.DataFrame:
    """Every ranked town: damage per hexagon joined with every score component from its fused hexes."""
    frames = []
    for f in sorted((paths.root / "results" / _set(flood_source, buildings_source)).glob("*/ranking_*.csv")):
        key = f.stem.removeprefix("ranking_")
        dmg = pd.read_csv(f)[["h3", "severe", "affected"]]
        hexes = pd.read_csv(f.parent / f"hexes_{key}.csv")
        df = dmg.merge(hexes[[c for c in ("h3", *COMPONENTS) if c in hexes]], on="h3", how="left")
        frames.append(df.assign(event=f.parent.name, town=f"{f.parent.name}/{key}"))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["cut_off"] = df.cut_off.fillna(False).astype(bool)
    return df.assign(priority_people=people_priority(df))


def people_exposure(df: pd.DataFrame) -> pd.Series:
    """Exposure measured in GHSL residents instead of buildings: people in the flood, plus everyone in a
    hexagon the flood cuts off."""
    inside = df.people_in_flood.fillna(0)
    stranded = np.where(df.cut_off, np.maximum(df.people_total.fillna(0) - inside, 0), 0)
    return pd.Series(1.0 - np.exp(-(inside + stranded) / PEOPLE_SCALE), index=df.index)


def weighted(df: pd.DataFrame, w: tuple[float, float, float], exposure: pd.Series | None = None) -> pd.Series:
    """The gated priority with weights ``w`` = (exposure, hazard, access)."""
    e = df.exposure.fillna(0) if exposure is None else exposure
    gate = (df.hazard.fillna(0) > 0) | df.cut_off
    return gate * (w[0] * e + w[1] * df.hazard.fillna(0) + w[2] * df.access.fillna(0))


def people_priority(df: pd.DataFrame) -> pd.Series:
    return weighted(df, (0.45, 0.30, 0.25), people_exposure(df))


def simplex(step: float = 0.05) -> list[tuple[float, float, float]]:
    n = round(1 / step)
    return [(i / n, j / n, (n - i - j) / n) for i in range(n + 1) for j in range(n + 1 - i)]


def risk(df: pd.DataFrame, access_weight: float, exposure: pd.Series | None = None) -> pd.Series:
    """Risk-style score: exposure scaled by hazard (the standard hazard x exposure form), with access added at
    ``access_weight``. A fully flooded meadow (no exposure) and an intact street (no hazard) both score low."""
    e = df.exposure.fillna(0) if exposure is None else exposure
    h = df.hazard.fillna(0)
    gate = (h > 0) | df.cut_off
    return gate * ((1 - access_weight) * e * (0.5 + 0.5 * h) + access_weight * df.access.fillna(0))


def candidates(df: pd.DataFrame, step: float = 0.05) -> list[tuple[str, dict[str, Any], pd.Series]]:
    """Every scoring rule the fit may choose from: additive weights on the simplex and the risk form, each with
    building- or people-based exposure."""
    exposures: dict[str, pd.Series | None] = {"buildings": None, "people": people_exposure(df)}
    out: list[tuple[str, dict[str, Any], pd.Series]] = []
    for name, e in exposures.items():
        for w in simplex(step):
            out.append(("additive", {"exposure": name, "weights": [round(x, 2) for x in w]}, weighted(df, w, e)))
        for a in np.round(np.arange(0.0, 0.51, step), 2):
            out.append(("risk", {"exposure": name, "access_weight": float(a)}, risk(df, float(a), e)))
    return out


def leave_one_event_out(df: pd.DataFrame, step: float = 0.05) -> dict[str, Any]:
    """Choose the scoring rule (family, exposure measure, weights) on all events but one; score the held-out
    event with it, next to the hand-set default and the single-layer baselines.

    The comparison that matters is the held-out column: a rule fitted on other floods, judged on a flood it never
    saw. If no fitted rule beats the default or the baselines out of sample, that is the finding.
    """
    scored = candidates(df, step)
    out: dict[str, Any] = {}
    for held in sorted(df.event.unique()):
        train_mask = (df.event != held).to_numpy()
        test = df[~train_mask]
        if train_mask.sum() == 0 or test.severe.sum() == 0:
            continue
        train = df[train_mask]

        def fit_score(c: tuple[str, dict[str, Any], pd.Series], train: pd.DataFrame = train) -> float:
            return macro_capture(train.assign(_s=c[2][train.index]), "_s")

        family, params, series = max(scored, key=fit_score)
        out[held] = {
            "chosen_family": family,
            "chosen": params,
            "train_capture": round(fit_score((family, params, series)), 3),
            "held_out_fitted": round(macro_capture(test.assign(_s=series[test.index]), "_s"), 3),
            "held_out_default": round(macro_capture(test, "priority"), 3),
            "held_out_buildings_in_flood": round(macro_capture(test, "buildings_in_flood"), 3),
            "held_out_people_in_flood": round(macro_capture(test, "people_in_flood"), 3),
        }
    if out:
        cols = ("held_out_fitted", "held_out_default", "held_out_buildings_in_flood", "held_out_people_in_flood")
        out["mean_over_events"] = {c: round(float(np.mean([v[c] for v in out.values() if c in v])), 3) for c in cols}
    return out


def blocks(cells: pd.Series, resolution: int = BLOCK_RESOLUTION) -> pd.Series:
    import h3

    return cells.map(lambda c: h3.cell_to_parent(c, resolution))


def _capture_top(df: pd.DataFrame, score: str, target: str = "severe", frac: float = TOP) -> float:
    return capture(df[score].fillna(0).astype(float), df[target].fillna(0), (frac,))[f"top{int(frac * 100)}"]


def macro_capture(df: pd.DataFrame, score: str, target: str = "severe", frac: float = TOP) -> float:
    """Mean over towns of the share of their damage in their own top ``frac`` of hexagons."""
    vals = [_capture_top(g, score, target, frac) for _, g in df.groupby("town") if g[target].sum() > 0]
    return float(np.mean(vals)) if vals else float("nan")


def ablation_scores(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Every variant of the score, built from the stored components so no stage has to rerun.

    E = exposure, H = hazard, A = access. Additive variants keep the default weights' proportions among the terms
    they use; ``risk`` is E x (0.5 + 0.5 H), the hazard-times-exposure form.
    """
    e_b, e_p = df.exposure.fillna(0), people_exposure(df)
    out: dict[str, pd.Series] = {}
    for tag, e in (("", e_b), ("people:", e_p)):
        terms = {"E": (0.45, e), "H": (0.30, df.hazard.fillna(0)), "A": (0.25, df.access.fillna(0))}
        for combo in ("E", "H", "A", "EH", "EA", "HA", "EHA"):
            total = sum(terms[c][0] for c in combo)
            w = tuple(terms[c][0] / total if c in combo else 0.0 for c in "EHA")
            out[f"{tag}{combo}"] = weighted(df, w, e)  # type: ignore[arg-type]
        out[f"{tag}risk"] = risk(df, 0.0, e)
        out[f"{tag}risk+A"] = risk(df, 0.25, e)
    out["water"] = df.satellite_water_fraction.fillna(0)
    out["buildings_in_flood"] = df.buildings_in_flood.fillna(0)
    out["people_in_flood"] = df.people_in_flood.fillna(0)
    return out


def per_event_ablation(df: pd.DataFrame, scores: dict[str, pd.Series]) -> dict[str, Any]:
    """Macro capture of each variant within each event, with the mean and the worst event: a score that is good
    on average but fails one kind of flood shows up here, where the pooled number hides it."""
    out: dict[str, Any] = {}
    for k, v in scores.items():
        d = df.assign(_s=v)
        by = {e: round(macro_capture(g, "_s"), 3) for e, g in d.groupby("event")}
        vals = np.array(list(by.values()), dtype=float)
        worst = min(by, key=lambda e: by[e])
        out[k] = {"events": by, "mean": round(float(np.nanmean(vals)), 3), "min": round(float(np.nanmin(vals)), 3),
                  "worst_event": worst}  # fmt: skip
    return out


def bootstrap(
    df: pd.DataFrame,
    n: int = BOOTSTRAP,
    seed: int = 0,
    target: str = "severe",
    scores: dict[str, pd.Series] | None = None,
    reference: str = "priority",
) -> dict[str, Any]:
    """Spatial block bootstrap of macro top-10 % capture for each score, and of ``reference`` minus each other
    score. ``scores`` maps names to extra score columns (an ablation); by default the built-in ``SCORES``."""
    rng = np.random.default_rng(seed)
    if scores:
        df = df.assign(**{f"_s:{k}": v for k, v in scores.items()})
        names = {k: f"_s:{k}" for k in scores}
    else:
        names = {s_: s_ for s_ in SCORES if s_ in df}
    df = df.assign(block=blocks(df.h3))
    by_town = {t: dict(tuple(g.groupby("block"))) for t, g in df.groupby("town")}
    point = {k: macro_capture(df, col, target) for k, col in names.items()}
    draws: dict[str, list[float]] = {s: [] for s in point}
    for _ in range(n):
        parts = []
        for t, bl in by_town.items():
            keys = list(bl)
            pick = rng.integers(0, len(keys), len(keys))
            parts.append(pd.concat([bl[keys[i]] for i in pick], ignore_index=True).assign(town=t))
        sample = pd.concat(parts, ignore_index=True)
        for k, col in names.items():
            draws[k].append(macro_capture(sample, col, target))
    out: dict[str, Any] = {}
    for s, vals in draws.items():
        v = np.array(vals)
        out[s] = {"top10": round(point[s], 3), "ci95": [round(float(np.nanpercentile(v, 2.5)), 3),
                                                       round(float(np.nanpercentile(v, 97.5)), 3)]}  # fmt: skip
    if reference in draws:
        p = np.array(draws[reference])
        for b in BASELINES if not scores else [k for k in draws if k != reference]:
            if b in draws:
                d = p - np.array(draws[b])
                out[f"{reference}_minus_{b}"] = {
                    "diff": round(point[reference] - point[b], 3),
                    "ci95": [round(float(np.nanpercentile(d, 2.5)), 3), round(float(np.nanpercentile(d, 97.5)), 3)],
                    "share_of_draws_priority_better": round(float(np.mean(d > 0)), 3),
                }
    return out


def summarise(paths: Paths, n: int = BOOTSTRAP) -> dict[str, Any]:
    df = load_rankings(paths)
    if df.empty:
        raise FileNotFoundError("no results/benchmark/*/ranking_*.csv yet; run `floodline benchmark` first")
    towns = (
        df.groupby("town")
        .apply(lambda g: pd.Series({"hexes": len(g), "severe": int(g.severe.sum()),
                                    **{s: _capture_top(g, s) for s in SCORES if s in g}}), include_groups=False)
        .reset_index()
    )  # fmt: skip
    towns.to_csv(paths.results / "benchmark_towns.csv", index=False)
    roads = [pd.read_csv(f) for f in (paths.root / "results" / "benchmark").glob("*/roads.csv") if f.stat().st_size > 1]
    road_pooled: list[dict[str, Any]] = []
    if roads:
        r = pd.concat(roads, ignore_index=True)
        g = r.groupby(["target", "threshold_m"])[["tp", "fp", "fn"]].sum().reset_index()
        g["precision"] = (g.tp / (g.tp + g.fp)).round(3)
        g["recall"] = (g.tp / (g.tp + g.fn)).round(3)
        g["f1"] = (2 * g.precision * g.recall / (g.precision + g.recall)).round(3)
        g.to_csv(paths.results / "benchmark_roads.csv", index=False)
        road_pooled = g.to_dict("records")
    summary = {
        "events": sorted(df.event.unique().tolist()),
        "towns": int(df.town.nunique()),
        "hexes": len(df),
        "severe_buildings": int(df.severe.sum()),
        "macro_top10_severe": bootstrap(df, n=n, target="severe"),
        "per_event_macro_top10": {
            e: {s: round(macro_capture(g, s), 3) for s in SCORES if s in g} for e, g in df.groupby("event")
        },
        "roads": road_pooled,
        "leave_one_event_out": leave_one_event_out(df),
        "ablation": bootstrap(df, n=n, scores=ablation_scores(df), reference="EHA"),
        "ablation_per_event": per_event_ablation(df, ablation_scores(df)),
    }
    # Data ablations: the same towns re-run with another flood source or building source.
    data_ablations: dict[str, Any] = {}
    summary["data_ablations"] = data_ablations
    for label, flood, built in (
        ("sentinel1_change", "sentinel1", "max"),
        ("sentinel1_otsu", "sentinel1_otsu", "max"),
        ("osm_buildings_only", "ems", "osm"),
    ):
        alt = load_rankings(paths, flood, built)
        if alt.empty:
            continue
        towns_both = sorted(set(alt.town) & set(df.town))
        a, e = alt[alt.town.isin(towns_both)], df[df.town.isin(towns_both)]
        sa, se = ablation_scores(a), ablation_scores(e)
        water_a = a.groupby("town").satellite_water_fraction.mean()
        water_e = e.groupby("town").satellite_water_fraction.mean()
        data_ablations[label] = {
            "towns": towns_both,
            "default": {k: round(macro_capture(e.assign(_s=v), "_s"), 3) for k, v in se.items()},
            "ablated": {k: round(macro_capture(a.assign(_s=v), "_s"), 3) for k, v in sa.items()},
            # Where it breaks, and why: per-event capture of the default score, and how much of each town the
            # flood layer calls water against the analysts' map.
            "per_event_EHA": {
                "default": per_event_ablation(e, {"EHA": se["EHA"]})["EHA"],
                "ablated": per_event_ablation(a, {"EHA": sa["EHA"]})["EHA"],
            },
            "water_share": {
                t: {"ablated": round(float(water_a[t]), 3), "default": round(float(water_e[t]), 3)} for t in towns_both
            },
            "water_share_median": {
                "ablated": round(float(water_a.median()), 3),
                "default": round(float(water_e.median()), 3),
            },
        }
    RunManifest("benchmark_summary", params={"bootstrap": n, "block_resolution": BLOCK_RESOLUTION}).finish(
        **summary
    ).write(paths.results / "benchmark_summary.json")
    log.info(
        "benchmark: %s events, %s towns, %s severe buildings",
        df.event.nunique(),
        df.town.nunique(),
        int(df.severe.sum()),
    )
    return summary
