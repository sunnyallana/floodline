"""Draw the paper's data figures as vector PDFs from the result files. Run from the repo root:

    .venv/Scripts/python paper/figures/make_figures.py

Needs matplotlib (not a package dependency; `pip install matplotlib`). Colours: cividis for the heatmap and the
Okabe-Ito palette for lines and bars, both readable with the common colour-vision deficiencies.

Inputs                                          Output
results/benchmark_summary.json (ablation_per_event)  fig_events.pdf     per-event capture heatmap
results/benchmark_summary.json (ablation)            fig_pooled.pdf     pooled capture with block-bootstrap CIs
results/benchmark_summary.json (roads)               fig_roads.pdf      closure rule vs. overlap threshold
results/agreement_euflood_distances.csv              fig_distances.pdf  photo distances to flood and river
"""

from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
COL_W, FULL_W = 3.45, 7.0  # inches: IEEE column and text width

OKABE = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "green": "#009E73",
    "vermillion": "#D55E00",
    "sky": "#56B4E9",
    "purple": "#CC79A7",
    "yellow": "#F0E442",
    "black": "#000000",
}

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "STIXGeneral", "DejaVu Serif"],
        "mathtext.fontset": "stix",
        "font.size": 8,
        "axes.titlesize": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "pdf.fonttype": 42,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.6,
    }
)

# Scores shown, in the order the paper discusses them, with the labels used in the text.
SCORES = [
    ("EHA", "Fused priority (default)"),
    ("EH", "Fused, without access"),
    ("risk", "Risk: E × (0.5 + 0.5 H)"),
    ("buildings_in_flood", "Buildings in the flood"),
    ("people_in_flood", "People in the flood"),
    ("E", "Exposure alone"),
    ("water", "Water extent alone"),
    ("A", "Access alone"),
]
EVENTS = [
    ("EMSR517", "Germany\n2021"),
    ("EMSR631", "Sanghar\n2022"),
    ("EMSR504", "NSW\n2021"),
    ("EMSR559", "Moramanga\n2022"),
    ("EMSR634", "Marche\n2022"),
    ("EMSR649", "Pachino\n2023"),
]


def two(v: float) -> str:
    """Two decimals, half up: the same rounding as make_numbers.py, so figures and text agree."""
    return str(Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def summary() -> dict:
    d = json.loads((ROOT / "results" / "benchmark_summary.json").read_text(encoding="utf-8"))
    return d.get("metrics", d)


def fig_events(m: dict) -> None:
    pe = m["ablation_per_event"]
    rows = [k for k, _ in SCORES]
    cols = [e for e, _ in EVENTS]
    data = np.array([[pe[r]["events"][c] for c in cols] + [pe[r]["mean"], pe[r]["min"]] for r in rows])
    fig, ax = plt.subplots(figsize=(FULL_W, 2.45))
    im = ax.imshow(data, cmap="cividis", vmin=0, vmax=1, aspect="auto")
    ax.set_xticks(range(len(cols) + 2), [lbl for _, lbl in EVENTS] + ["Mean of\nevents", "Worst\nevent"])
    ax.set_yticks(range(len(rows)), [lbl for _, lbl in SCORES])
    ax.tick_params(length=0)
    ax.axvline(len(cols) - 0.5, color="white", lw=2)
    for (i, j), v in np.ndenumerate(data):
        ax.text(j, i, two(v), ha="center", va="center", fontsize=7, color="black" if v > 0.55 else "white")
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.025, pad=0.01)
    cb.set_label("Share of damaged or destroyed\nbuildings in the top 10% of hexagons")
    cb.outline.set_linewidth(0.4)
    fig.tight_layout()
    fig.savefig(OUT / "fig_events.pdf")
    plt.close(fig)


def fig_pooled(m: dict) -> None:
    ab = m["ablation"]
    keys = [k for k, _ in SCORES]
    vals = np.array([ab[k]["top10"] for k in keys])
    lo = np.array([ab[k]["ci95"][0] for k in keys])
    hi = np.array([ab[k]["ci95"][1] for k in keys])
    y = np.arange(len(keys))[::-1]
    colours = (
        [OKABE["blue"]] * 2
        + [OKABE["sky"]]
        + [OKABE["orange"]] * 2
        + [OKABE["green"], OKABE["vermillion"], OKABE["purple"]]
    )
    fig, ax = plt.subplots(figsize=(COL_W, 2.2))
    ax.barh(y, vals, color=colours, height=0.65)
    ax.errorbar(vals, y, xerr=[vals - lo, hi - vals], fmt="none", ecolor="black", elinewidth=0.7, capsize=2)
    ax.axvline(0.10, color="black", lw=0.6, ls=":")
    ax.text(0.11, y[0] + 0.45, "random", fontsize=6.5, va="bottom")
    ax.set_yticks(y, [lbl for _, lbl in SCORES])
    ax.set_xlim(0, 1)
    ax.set_ylim(y[-1] - 0.6, y[0] + 1.1)
    ax.set_xlabel("Pooled top-10% capture, 95% block-bootstrap CI")
    ax.tick_params(axis="y", length=0)
    fig.tight_layout()
    fig.savefig(OUT / "fig_pooled.pdf")
    plt.close(fig)


def fig_roads(m: dict) -> None:
    r = pd.DataFrame(m["roads"])
    fig, ax = plt.subplots(figsize=(COL_W, 1.9))
    for target, ls, name in (("affected", "-", "any grade"), ("severe", "--", "severe")):
        t = r[r.target == target].sort_values("threshold_m")
        x = t.threshold_m.clip(lower=0.5)  # log axis: 0 m drawn at 0.5 m
        ax.plot(x, t.precision, ls, color=OKABE["blue"], marker="o", ms=2.5, lw=1, label=f"precision, {name}")
        ax.plot(x, t.recall, ls, color=OKABE["vermillion"], marker="s", ms=2.5, lw=1, label=f"recall, {name}")
    ax.axvline(5, color="black", lw=0.6, ls=":")
    ax.text(5.4, 0.05, "5 m (used)", fontsize=6.5)
    ax.set_xscale("log")
    ticks = sorted(r.threshold_m.unique())
    ax.set_xticks([max(t, 0.5) for t in ticks], ["0" if t == 0 else f"{t:g}" for t in ticks])
    ax.minorticks_off()
    ax.set_ylim(0, 1)
    ax.set_xlabel("Length of road inside the flood needed to close it (m)")
    ax.legend(ncol=2, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), handlelength=2.0, columnspacing=1.0)
    fig.tight_layout()
    fig.savefig(OUT / "fig_roads.pdf")
    plt.close(fig)


def fig_distances() -> None:
    d = pd.read_csv(ROOT / "results" / "agreement_euflood_distances.csv")
    fig, ax = plt.subplots(figsize=(COL_W, 2.0))
    for act, colour, name in (
        ("EMSR044", OKABE["blue"], "Dresden area"),
        ("EMSR045", OKABE["vermillion"], "Prague area"),
    ):
        u = d[d.ems_activation == act].drop_duplicates("location_key")
        for col, ls, what in (("d_flood_m", "-", "to mapped flood"), ("d_permanent_m", "--", "to river channel")):
            x = np.sort(np.maximum(u[col].to_numpy(float), 1.0))
            ax.plot(x, np.arange(1, len(x) + 1) / len(x), ls, color=colour, lw=1, label=f"{name}, {what}")
    ax.set_xscale("log")
    ax.set_xlim(1, 1e4)
    ax.set_ylim(0, 1)
    ax.set_xlabel("Distance per unique geotag (m)")
    ax.set_ylabel("Cumulative share")
    ax.legend(ncol=2, frameon=False, loc="lower left", bbox_to_anchor=(0, 1.0), fontsize=6.5, columnspacing=1.0)
    fig.tight_layout()
    fig.savefig(OUT / "fig_distances.pdf")
    plt.close(fig)


def main() -> None:
    m = summary()
    fig_events(m)
    fig_pooled(m)
    fig_roads(m)
    fig_distances()
    print("wrote", ", ".join(p.name for p in sorted(OUT.glob("fig_*.pdf"))))


if __name__ == "__main__":
    main()
