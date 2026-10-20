"""Write paper/numbers.tex: one LaTeX macro per number the paper quotes, read from the result files.

Run from the repo root:  .venv/Scripts/python paper/make_numbers.py

Rules:
- The paper typesets results only through these macros, so every number traces to a file and a key.
- A missing file or key never becomes a guess: the macro expands to \\pending{<what is missing>}, rendered grey.
- Runs that are still in progress (data ablations, VLM benchmark) count as missing until their manifest says done.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
OUT = Path(__file__).resolve().parent / "numbers.tex"
DEPTH_ORDER = ["dry", "ankle", "knee", "waist", "chest", "above_head"]


class MissingResultError(Exception):
    pass


def load(rel: str) -> dict[str, Any]:
    path = ROOT / rel
    if not path.exists():
        raise MissingResultError(f"{rel} not found")
    d = json.loads(path.read_text(encoding="utf-8"))
    return d.get("metrics", d)


def dig(d: Any, *keys: Any) -> Any:
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            raise MissingResultError(f"key {'/'.join(map(str, keys))}")
        d = d[k]
    return d


def fx(v: float, nd: int = 2) -> str:
    q = Decimal(1).scaleb(-nd)
    return str(Decimal(str(v)).quantize(q, rounding=ROUND_HALF_UP))


def signed(v: float, nd: int = 3) -> str:
    """Signed difference with a typographic minus."""
    s = fx(v, nd)
    return "$-$" + s[1:] if s.startswith("-") else "$+$" + s


def thousands(n: float) -> str:
    return f"{round(n):,}".replace(",", "{,}")


def pct(v: float, nd: int = 1) -> str:
    return fx(100 * v, nd) + r"\%"


macros: list[tuple[str, str, str]] = []  # (name, value, source)


def m(name: str, source: str, fn: Callable[[], str], pending: str | None = None) -> None:
    assert name.isalpha(), name
    try:
        value = fn()
    except MissingResultError as e:
        label = (pending or name).replace("_", r"\_").replace("%", r"\%")
        value = r"\pending{" + label + "}"
        source = f"{source} -- missing: {e}"
    macros.append((name, value, source))


BENCH = "results/benchmark_summary.json"


def bench(*keys: Any) -> Any:
    return dig(load(BENCH), *keys)


# ---------------------------------------------------------------- benchmark scale
m("nEvents", BENCH, lambda: str(len(bench("events"))))
m("nTowns", BENCH, lambda: str(bench("towns")))
m("nHexes", BENCH, lambda: thousands(bench("hexes")))
m("nSevere", BENCH, lambda: thousands(bench("severe_buildings")))

m(
    "nDraws",
    BENCH,
    lambda: thousands(dig(json.loads((ROOT / BENCH).read_text(encoding="utf-8")), "params", "bootstrap")),
)
m(
    "blockRes",
    BENCH,
    lambda: str(dig(json.loads((ROOT / BENCH).read_text(encoding="utf-8")), "params", "block_resolution")),
)
m("wlImages", "results/waterline_run.json", lambda: str(dig(load("results/waterline_run.json"), "images")))

# ---------------------------------------------------------------- RQ1/RQ2: pooled capture with block-bootstrap CI
VARIANTS = {
    "EHA": "EHA",
    "EH": "EH",
    "EA": "EA",
    "HA": "HA",
    "Risk": "risk",
    "RiskA": "risk+A",
    "Buildings": "buildings_in_flood",
    "People": "people_in_flood",
    "E": "E",
    "Water": "water",
    "A": "A",
    "PeopleEHA": "people:EHA",
}
for short, key in VARIANTS.items():
    m(f"cap{short}", BENCH, lambda key=key: fx(bench("ablation", key, "top10")))
    m(f"cap{short}Lo", BENCH, lambda key=key: fx(bench("ablation", key, "ci95")[0]))
    m(f"cap{short}Hi", BENCH, lambda key=key: fx(bench("ablation", key, "ci95")[1]))
    if short != "EHA":
        dk = f"EHA_minus_{key}"
        m(f"d{short}", BENCH, lambda dk=dk: signed(bench("ablation", dk, "diff")))
        m(f"d{short}Lo", BENCH, lambda dk=dk: signed(bench("ablation", dk, "ci95")[0]))
        m(f"d{short}Hi", BENCH, lambda dk=dk: signed(bench("ablation", dk, "ci95")[1]))
        m(f"p{short}", BENCH, lambda dk=dk: pct(bench("ablation", dk, "share_of_draws_priority_better"), 0))

# ---------------------------------------------------------------- RQ3: per-event worst case and leave-one-event-out
EVENT_NAME = {
    "EMSR504": "New South Wales",
    "EMSR517": "western Germany",
    "EMSR559": "Moramanga",
    "EMSR631": "Sanghar",
    "EMSR634": "Marche",
    "EMSR649": "Pachino",
}
for short, key in VARIANTS.items():
    m(f"mean{short}", BENCH, lambda key=key: fx(bench("ablation_per_event", key, "mean")))
    m(f"worst{short}", BENCH, lambda key=key: fx(bench("ablation_per_event", key, "min")))
    m(f"worst{short}Event", BENCH, lambda key=key: EVENT_NAME[bench("ablation_per_event", key, "worst_event")])

LOEO = ("leave_one_event_out",)
for short, key in {
    "Default": "held_out_default",
    "Fitted": "held_out_fitted",
    "Buildings": "held_out_buildings_in_flood",
    "People": "held_out_people_in_flood",
}.items():
    m(f"loeo{short}", BENCH, lambda key=key: fx(bench(*LOEO, "mean_over_events", key), 3))
for ev, short in (("EMSR559", "Moramanga"), ("EMSR631", "Sanghar")):
    m(f"loeoFitted{short}", BENCH, lambda ev=ev: fx(bench(*LOEO, ev, "held_out_fitted")))


def access_zero() -> str:
    folds = {k: v for k, v in bench(*LOEO).items() if k.startswith("EMSR")}
    zero = 0
    for v in folds.values():
        ch = v["chosen"]
        w = ch.get("access_weight", ch.get("weights", [None, None, None])[-1])
        zero += int(w == 0)
    return f"{zero} of {len(folds)}"


m("loeoAccessZero", BENCH, access_zero)
SENS = "results/sensitivity_run.json"
m("sensVariants", SENS, lambda: str(dig(load(SENS), "variants")))
m("sensPragueSpearmanMin", SENS, lambda: fx(dig(load(SENS), "hexes_prague", "spearman_live", "min")))
m("sensPragueKeepEight", SENS, lambda: pct(dig(load(SENS), "hexes_prague", "share_keeping_8_of_top10"), 0))

# ---------------------------------------------------------------- road closure rule against EMS road grading
for t, word in ((0.0, "Zero"), (5.0, "Five"), (15.0, "Fifteen"), (50.0, "Fifty")):
    for target, tw in (("affected", "Aff"), ("severe", "Sev")):

        def row(t=t, target=target):
            for r in bench("roads"):
                if r["target"] == target and float(r["threshold_m"]) == t:
                    return r
            raise MissingResultError(f"roads {target} {t}")

        for k, kw in (("precision", "P"), ("recall", "R"), ("f1", "F")):
            m(f"road{tw}{kw}{word}", BENCH, lambda row=row, k=k: fx(row()[k]))

# ---------------------------------------------------------------- RQ4: data ablations (in progress -> pending)


def ablation_done(name: str) -> dict[str, Any]:
    d = bench("data_ablations", name)
    towns = d.get("towns", [])
    if not d.get("complete") and len(towns) < bench("towns"):
        raise MissingResultError(f"{name}: {len(towns)} of {bench('towns')} towns so far")
    return d


for name, short in (("sentinel1_change", "Change"), ("sentinel1_otsu", "Otsu"), ("osm_buildings_only", "OSMOnly")):
    for variant, vshort in (("EHA", "EHA"), ("buildings_in_flood", "Buildings"), ("water", "Water")):
        m(
            f"abl{short}{vshort}",
            BENCH,
            lambda name=name, variant=variant: fx(dig(ablation_done(name), "ablated", variant)),
            pending=f"{name}: {variant}",
        )
        m(
            f"abl{short}{vshort}Ref",
            BENCH,
            lambda name=name, variant=variant: fx(dig(ablation_done(name), "default", variant)),
            pending=f"{name}: reference {variant}",
        )


def share_pct(v: float) -> str:
    return rf"{100 * v:.1f}\%" if v < 0.1 else rf"{fx(100 * v, 0)}\%"


# how much of each town the radar calls water, against the analysts' map (median over towns)
for name, short in (("sentinel1_change", "Change"), ("sentinel1_otsu", "Otsu")):
    m(
        f"waterMed{short}",
        BENCH,
        lambda name=name: share_pct(dig(ablation_done(name), "water_share_median", "ablated")),
        pending=f"{name}: water share",
    )
m(
    "waterMedEMS",
    BENCH,
    lambda: share_pct(dig(ablation_done("sentinel1_change"), "water_share_median", "default")),
    pending="EMS water share",
)
# where OSM-only buildings cost the most: per-event capture of the default score
for e, short in (("EMSR504", "NSW"), ("EMSR631", "Sanghar")):
    for side, sshort in (("default", "Ref"), ("ablated", "")):
        m(
            f"osmOnly{short}{sshort}",
            BENCH,
            lambda e=e, side=side: fx(dig(ablation_done("osm_buildings_only"), "per_event_EHA", side, "events", e)),
            pending=f"OSM-only {e}",
        )

# physical findings behind the change-detection failures: scene dates and water shares (final, not in progress)
S1 = "results/benchmark_s1/{e}/event_run.json"
EMS = "results/benchmark/{e}/satellite_run_{a}.json"
for e, a, short in (("EMSR517", "aoi07", "Erftstadt"), ("EMSR631", "aoi01", "Sanghar")):
    m(
        f"radar{short}Share",
        S1.format(e=e),
        lambda e=e, a=a: pct(dig(load(S1.format(e=e)), "towns", a, "radar", "water_share_of_area")),
    )
    m(
        f"radar{short}Scene",
        S1.format(e=e),
        lambda e=e, a=a: dig(load(S1.format(e=e)), "towns", a, "radar", "acquired")[:10],
    )
    m(
        f"radar{short}Pre",
        S1.format(e=e),
        lambda e=e, a=a: (lambda s: f"{s[17:21]}-{s[21:23]}-{s[23:25]}")(
            dig(load(S1.format(e=e)), "towns", a, "radar", "pre_scene")
        ),
    )
    m(
        f"ems{short}Share",
        EMS.format(e=e, a=a),
        lambda e=e, a=a: pct(dig(load(EMS.format(e=e, a=a)), "flood_km2") / dig(load(EMS.format(e=e, a=a)), "aoi_km2")),
    )

# ---------------------------------------------------------------- RQ5: trust and attacks
TR = "results/trust_eval.json"
m("trustRefs", TR, lambda: thousands(dig(load(TR), "reference_images")))
m("trustSample", TR, lambda: str(dig(load(TR), "sample")))
m("trustForwarded", TR, lambda: pct(dig(load(TR), "recall_forwarded")))
m("trustCropped", TR, lambda: pct(dig(load(TR), "recall_cropped_10pct")))
m("trustBandN", TR, lambda: str(dig(load(TR), "euflood_ambiguous_3_to_6_bits")))
m("trustEufloodN", TR, lambda: thousands(dig(load(TR), "euflood_photos")))
m("trustBandShare", TR, lambda: pct(dig(load(TR), "euflood_ambiguous_3_to_6_bits") / dig(load(TR), "euflood_photos")))
m("trustRejectBits", TR, lambda: str(dig(load(TR), "reject_bits")))
m("leakEuflood", TR, lambda: thousands(dig(load(TR), "euflood_in_cvfd_le2_bits")))
m("leakTest", TR, lambda: str(dig(load(TR), "cvfd_split_leakage", "test", "near_copy_in_train")))
m("leakTestN", TR, lambda: thousands(dig(load(TR), "cvfd_split_leakage", "test", "images")))
for f, pre in (("results/attack_prague.json", "atk"), ("results/attack_prague_permissive.json", "perm")):
    for sc, s in (("lone", "Lone"), ("colluding", "Collude"), ("wet_exaggeration", "Wet")):
        m(f"{pre}{s}Overlap", f, lambda f=f, sc=sc: fx(dig(load(f), sc, "top10_overlap", "mean")))
        m(
            f"{pre}{s}Flagged",
            f,
            lambda f=f, sc=sc: (
                f"{fx(dig(load(f), sc, 'targets_flagged', 'mean'), 0)}/{fx(dig(load(f), sc, 'targets', 'mean'), 0)}"
            ),
        )
        m(
            f"{pre}{s}InTop",
            f,
            lambda f=f, sc=sc: fx(dig(load(f), sc, "targets_in_top", "mean"), 2).rstrip("0").rstrip("."),
        )
m("atkDraws", "results/attack_prague.json", lambda: str(dig(load("results/attack_prague.json"), "draws")))

# ---------------------------------------------------------------- perception
SAM = "results/sam3_run.json"
m("samImages", SAM, lambda: str(dig(load(SAM), "images")))
m("samCalls", SAM, lambda: str(dig(load(SAM), "calls")))
for cls, s in (("water", "Water"), ("person", "Person"), ("building", "Building")):
    m(f"sam{s}Mean", SAM, lambda cls=cls: fx(dig(load(SAM), "summary", f"{cls}_iou", "mean"), 3))
    m(f"sam{s}Median", SAM, lambda cls=cls: fx(dig(load(SAM), "summary", f"{cls}_iou", "median"), 3))
    m(f"sam{s}N", SAM, lambda cls=cls: str(dig(load(SAM), "summary", f"{cls}_iou", "n")))
m("samWaterBF", SAM, lambda: fx(dig(load(SAM), "summary", "water_boundary_f1", "mean"), 3))

WL = "results/waterline_run.json"
m("wlWithEstimate", WL, lambda: str(dig(load(WL), "with_estimate")))
m("wlN", WL, lambda: str(dig(load(WL), "benchmark", "n")))
m("wlExact", WL, lambda: fx(dig(load(WL), "benchmark", "exact")))
m("wlWithin", WL, lambda: fx(dig(load(WL), "benchmark", "within_one")))
m("wlMAE", WL, lambda: fx(dig(load(WL), "benchmark", "mean_abs_bins")))


def waterline_table() -> pd.DataFrame:
    w = pd.read_csv(RES / "waterline_depth.csv")
    lab = pd.read_csv(ROOT / "data" / "depth_labels.csv")
    j = lab.merge(w, on="image_id", suffixes=("_true", "_pred"))
    j = j[j.depth_bin_true.isin(DEPTH_ORDER) & j.depth_bin_pred.isin(DEPTH_ORDER)]
    run = dig(load(WL), "benchmark")
    t, p = j.depth_bin_true.map(DEPTH_ORDER.index), j.depth_bin_pred.map(DEPTH_ORDER.index)
    if len(j) != run["n"] or abs((t == p).mean() - run["exact"]) > 1e-9:
        raise MissingResultError("waterline CSV does not reproduce waterline_run.json")
    return j


def wl_base(kind: str) -> str:
    j = waterline_table()
    t, b = j.depth_bin_true.map(DEPTH_ORDER.index), DEPTH_ORDER.index("ankle")
    return fx((t == b).mean() if kind == "exact" else ((t - b).abs() <= 1).mean())


def wl_count(true: str, pred: str | None) -> str:
    j = waterline_table()
    sub = j[j.depth_bin_true == true]
    hit = sub if pred is None else sub[sub.depth_bin_pred == pred]
    return f"{len(hit)} of {len(sub)}" if pred else str(len(sub))


m("wlBaseExact", "results/waterline_depth.csv + data/depth_labels.csv", lambda: wl_base("exact"))
m("wlBaseWithin", "results/waterline_depth.csv + data/depth_labels.csv", lambda: wl_base("within"))
m("wlDryRight", "results/waterline_depth.csv + data/depth_labels.csv", lambda: wl_count("dry", "dry"))
m("wlAnkleKnee", "results/waterline_depth.csv + data/depth_labels.csv", lambda: wl_count("ankle", "knee"))
m("wlAboveHeadN", "results/waterline_depth.csv + data/depth_labels.csv", lambda: wl_count("above_head", None))
VLM = "results/vlm_benchmark.json"


def vlm(*keys: str) -> float:
    d = load(VLM)
    if not d.get("complete"):
        raise MissingResultError(f"{VLM}: run in progress ({d.get('images_assessed', '?')} images assessed)")
    return dig(d, *keys)


m("vlmN", VLM, lambda: str(vlm("n")), pending="VLM benchmark images")
m("vlmExact", VLM, lambda: fx(vlm("exact")), pending="two-pass Qwen3-VL-8B exact class accuracy")
m("vlmWithin", VLM, lambda: fx(vlm("within_one")), pending="two-pass Qwen3-VL-8B within-one accuracy")
m("vlmBaseExact", VLM, lambda: fx(vlm("baseline", "exact")), pending="majority baseline on the VLM-assessed images")

# ---------------------------------------------------------------- data and case studies
REL = "results/release_run.json"
m("depthImages", REL, lambda: str(dig(load(REL), "images")))
m("depthUnknown", REL, lambda: str(dig(load(REL), "classes", "unknown")))
EXT = "results/external_run.json"
m("eufRecords", EXT, lambda: thousands(dig(load(EXT), "records")))
m("eufLocated", EXT, lambda: str(dig(load(EXT), "located")))
AG = "results/agreement_euflood_run.json"
m("agInAoi", AG, lambda: str(dig(load(AG), "photos_in_mapped_area")))
m("agOwnHex", AG, lambda: pct(dig(load(AG), "all_mapped", "own_hex_wet")))
m("agRing", AG, lambda: pct(dig(load(AG), "all_mapped", "within_1_ring_wet")))
m("agRingPerm", AG, lambda: pct(dig(load(AG), "all_mapped", "within_1_ring_flood_or_permanent"), 0))
for act, s in (("EMSR044", "Dresden"), ("EMSR045", "Prague")):
    u = ("distances", act, "per_unique_location")
    m(f"ag{s}N", AG, lambda u=u: str(dig(load(AG), *u, "n")))
    m(f"ag{s}Flood", AG, lambda u=u: thousands(dig(load(AG), *u, "d_flood_m", "median")))
    m(f"ag{s}River", AG, lambda u=u: thousands(dig(load(AG), *u, "d_permanent_m", "median")))
    m(f"ag{s}Near", AG, lambda u=u: pct(dig(load(AG), *u, "within_150m_flood_or_permanent")))
RP, EP, HP = "results/reach_prague_run.json", "results/exposure_prague_run.json", "results/hexes_prague_run.json"
m("pragueClosed", RP, lambda: str(dig(load(RP), "closed_roads")))
m("pragueUnderWater", RP, lambda: fx(dig(load(RP), "road_under_water_km")))
m("pragueCut", RP, lambda: str(dig(load(RP), "hexes_cut_off")))
m("pragueBuildings", EP, lambda: str(dig(load(EP), "buildings_in_flood")))
m("pragueTopBuildings", HP, lambda: str(dig(load(HP), "top10")[0]["buildings_affected"]))
CITIES = "docs/map/cities.json"


def city(key: str) -> dict[str, Any]:
    path = ROOT / CITIES
    if not path.exists():
        raise MissingResultError(CITIES)
    for c in json.loads(path.read_text(encoding="utf-8")):
        if c["key"] == key:
            return c
    raise MissingResultError(f"{CITIES}: {key}")


m("praguePhotos", CITIES, lambda: str(city("prague")["stats"]["photos"]))
RL, EL, SL = "results/reach_larkana_run.json", "results/exposure_larkana_run.json", "results/sentinel1_larkana_run.json"
m("larkClosed", RL, lambda: thousands(dig(load(RL), "closed_roads")))
m("larkUnderWater", RL, lambda: thousands(dig(load(RL), "road_under_water_km")))
m("larkCut", RL, lambda: str(dig(load(RL), "hexes_cut_off")))
m("larkOSM", EL, lambda: str(dig(load(EL), "buildings_queried", "osm")))
m("larkMS", EL, lambda: thousands(dig(load(EL), "buildings_queried", "microsoft")))
m("larkHexes", EL, lambda: thousands(dig(load(EL), "target_hexes")))
m("larkBuildingsFlood", CITIES, lambda: thousands(city("larkana")["stats"]["buildings_in_flood"]))
m("sOneIoU", SL, lambda: fx(dig(load(SL), "iou")))
m("sOneP", SL, lambda: fx(dig(load(SL), "precision")))
m("sOneR", SL, lambda: fx(dig(load(SL), "recall")))
m("sOneShareRadar", SL, lambda: fx(dig(load(SL), "radar_water_share")))
m("sOneShareEMS", SL, lambda: fx(dig(load(SL), "ems_water_share")))
m("sOneSpearman", SL, lambda: fx(dig(load(SL), "hex_spearman")))
m("sOneHexes", SL, lambda: thousands(dig(load(SL), "hexes")))
m("sOneThreshold", SL, lambda: fx(dig(load(SL), "otsu_threshold_db"), 1))


TABLES = Path(__file__).resolve().parent / "tables"


def table_events() -> str:
    """Event table from docs/site.json (names, dates, kinds, towns) and results/benchmark_towns.csv (counts)."""
    site = json.loads((ROOT / "docs" / "site.json").read_text(encoding="utf-8"))
    towns = pd.read_csv(RES / "benchmark_towns.csv")
    towns["event"] = towns.town.str.split("/").str[0]
    rows = []
    for e in site["events"]:
        t = towns[towns.event == e["code"]]
        rows.append(
            f"{e['code']} & {e['name'] if e['country'] in e['name'] else e['name'] + ', ' + e['country']} & "
            f"{e['when']} & {e['kind']} & "
            f"{', '.join(e['towns'])} & {thousands(t.hexes.sum())} & {thousands(t.severe.sum())} \\\\"
        )
    total = (
        f"\\midrule\nTotal & & & & {len(towns)} towns & "
        f"{thousands(towns.hexes.sum())} & {thousands(towns.severe.sum())} \\\\"
    )
    head = (
        "\\setlength{\\tabcolsep}{4pt}\n\\begin{tabular}{@{}llllp{45mm}rr@{}}\n\\toprule\n"
        "Activation & Area & Date & Type & Towns & Hexagons & Damaged or destroyed \\\\\n\\midrule"
    )
    return (
        "% Generated by make_numbers.py from docs/site.json and results/benchmark_towns.csv\n"
        + head
        + "\n"
        + "\n".join(rows)
        + "\n"
        + total
        + "\n\\bottomrule\n\\end{tabular}\n"
    )


def table_loeo() -> str:
    folds = {k: v for k, v in bench("leave_one_event_out").items() if k.startswith("EMSR")}
    site = {
        e["code"]: e["name"] for e in json.loads((ROOT / "docs" / "site.json").read_text(encoding="utf-8"))["events"]
    }
    rows = []
    for code, v in folds.items():
        ch = v["chosen"]
        if v["chosen_family"] == "risk":
            rule = f"risk, $w_A$={ch['access_weight']:g}"
        else:
            rule = "($" + ",".join(f"{w:g}" for w in ch["weights"]) + "$)"
        rows.append(
            f"{site.get(code, code)} & {rule} & {fx(v['train_capture'])} & {fx(v['held_out_fitted'])} & "
            f"{fx(v['held_out_default'])} & {fx(v['held_out_buildings_in_flood'])} & "
            f"{fx(v['held_out_people_in_flood'])} \\\\"
        )
    mo = bench("leave_one_event_out", "mean_over_events")
    rows.append(
        f"\\midrule\nMean held out & & & {fx(mo['held_out_fitted'], 3)} & "
        f"\\textbf{{{fx(mo['held_out_default'], 3)}}} & "
        f"{fx(mo['held_out_buildings_in_flood'], 3)} & {fx(mo['held_out_people_in_flood'], 3)} \\\\"
    )
    head = (
        "\\begin{tabular}{@{}llrrrrr@{}}\n\\toprule\n"
        "Held out & Fitted rule & Train & Fitted & Default & Buildings & People \\\\\n\\midrule"
    )
    return (
        "% Generated by make_numbers.py from results/benchmark_summary.json (leave_one_event_out)\n"
        + head
        + "\n"
        + "\n".join(rows)
        + "\n\\bottomrule\n\\end{tabular}\n"
    )


def write_tables() -> None:
    TABLES.mkdir(exist_ok=True)
    for name, fn in (("events", table_events), ("loeo", table_loeo)):
        try:
            body = fn()
        except (MissingResultError, KeyError, FileNotFoundError) as e:
            body = f"\\pending{{table {name}: {str(e).replace('_', ' ')}}}\n"
        (TABLES / f"{name}.tex").write_text(body, encoding="utf-8")


def main() -> None:
    lines = [
        "% Generated by paper/make_numbers.py from the result files; do not edit by hand.",
        "% Each macro is followed by the file it was read from.",
    ]
    for name, value, source in macros:
        lines.append(f"\\newcommand{{\\{name}}}{{{value}}} % {source}")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    pending = [n for n, v, _ in macros if v.startswith(r"\pending")]
    write_tables()
    print(f"{len(macros)} macros -> {OUT.name}; pending: {', '.join(pending) or 'none'}; tables -> tables/")


if __name__ == "__main__":
    main()
