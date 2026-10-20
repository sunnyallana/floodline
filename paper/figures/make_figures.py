"""Write the data files the paper's pgfplots figures read. Run from the repo root:

    .venv/Scripts/python paper/figures/make_figures.py

Input: results/agreement_euflood_distances.csv (written by `floodline agree ... --ems-dirs ...`).
Output: paper/figures/cdf_<activation>_<target>.dat, one empirical CDF per activation and distance target,
computed per unique geotag (photos sharing a copied geotag count once), as in results/README.md section 5.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MAX_POINTS = 200  # enough for a smooth curve, small enough for LaTeX memory
FLOOR_M = 1.0  # log axis: distances below 1 m are drawn at 1 m


def ecdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    x = np.sort(np.maximum(values, FLOOR_M))
    y = np.arange(1, len(x) + 1) / len(x)
    if len(x) > MAX_POINTS:
        idx = np.unique(np.linspace(0, len(x) - 1, MAX_POINTS).round().astype(int))
        x, y = x[idx], y[idx]
    return x, y


def main() -> None:
    d = pd.read_csv(ROOT / "results" / "agreement_euflood_distances.csv")
    for activation, group in d.groupby("ems_activation"):
        unique = group.drop_duplicates("location_key")
        for target, col in (("flood", "d_flood_m"), ("river", "d_permanent_m")):
            x, y = ecdf(unique[col].to_numpy(dtype=float))
            path = OUT / f"cdf_{activation}_{target}.dat"
            pd.DataFrame({"d": x.round(1), "p": y.round(4)}).to_csv(path, sep=" ", index=False)
            print(f"{path.name}: n={len(unique)} median={np.median(unique[col]):.1f} m")


if __name__ == "__main__":
    main()
