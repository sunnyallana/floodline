# Floodline paper (draft)

IEEE conference format (`IEEEtran`, two columns), 6 pages. One result is still pending (red marker): the two-pass Qwen3-VL depth accuracy on the benchmark. The page budget is full: adding its number should replace text, not add it.

## Build

From this directory, with MiKTeX or TeX Live (`latexmk` needs Perl; the plain sequence works without it):

```bash
../.venv/Scripts/python ../paper/figures/make_figures.py   # from the repo root: refreshes the CDF data files
pdflatex floodline && bibtex floodline && pdflatex floodline && pdflatex floodline
```

`figures/make_figures.py` reads `results/agreement_euflood_distances.csv` and writes `figures/cdf_*.dat`, which
pgfplots draws in Fig. 3. The PNG figures are screenshots of the live web map (re-shot after the 5 m closure rule) and the field app.

## Before submission

- Every red `[pending: ...]` marker is a result the pipeline has not produced yet. Search the source for
  `\pending` and fill each from its result file; do not submit with any left.
- Each number in the text is followed by a `%` comment naming the file it came from. Two numbers come from `docs/cities.json` (written by `floodline serve`), not `results/`: the 203 Prague photos
  and the Larkana top-five building counts. Larkana buildings in flood: `results/exposure_larkana_run.json` says
- Larkana buildings in flood: resolved. 10,175 are in mapped hexagons (the figure the paper uses); the exposure run's 10,180 includes 5 buildings in 4 hexagons just outside the mapped area (results/README.md section 9).
- Authorship: the draft lists Sunny Shaban Ali as sole author. The CV lists the final-year paper with
  co-authors ("first author et al."), so decide whether the supervisor (Musawar Ali) or others should be added.
- The depth benchmark labels are described as AI-assisted with human review pending. Update the text if the
  review happens.
- Target venue page limits and anonymisation rules are not applied yet.

## References

All 25 entries in `references.bib` were checked against the publisher, arXiv or the project page. Long author
lists are truncated with "and others" where only the first authors were confirmed.
