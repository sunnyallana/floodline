# Floodline paper

IEEE conference format (`IEEEtran`, two columns), 7 pages including references. Structured around five research
questions on the six-event EMS damage-grading benchmark (`results/benchmark_summary.json`).

## Every number comes from a result file

`floodline.tex` contains no result numbers. They come from:

- `numbers.tex`: one `\newcommand` per number, written by `make_numbers.py`; each line ends with a comment
  naming the file it was read from;
- `tables/events.tex`, `tables/loeo.tex`: complete tables, written by the same script;
- `figures/fig_*.pdf`: data figures, drawn by `figures/make_figures.py` from the same files.

A missing file, a missing key or a run that is not finished (`complete` is not true; data ablations with fewer
towns than the benchmark) becomes `\pending{...}`, typeset as a grey "[pending: ...]" marker. Nothing is
estimated.

## Regenerate (from the repo root)

```bash
.venv/Scripts/python paper/make_numbers.py          # numbers.tex and tables/*.tex; prints what is still pending
.venv/Scripts/python paper/figures/make_figures.py  # figures/fig_*.pdf (needs matplotlib: pip install matplotlib)
cd paper
pdflatex floodline && bibtex floodline && pdflatex floodline && pdflatex floodline
```

`latexmk` needs Perl under MiKTeX; the plain sequence above works without it. The PNG figures are screenshots of
the live map (`map_prague.png`, `map_larkana.png`) and the field app (`app_*.png`).

## Still pending

Used in the text: `\ablChangeEHA`, `\ablChangeEHARef` (Sentinel-1 change detection), `\ablOtsuEHA`,
`\ablOtsuEHARef` (Otsu), `\ablOSMOnlyEHA`, `\ablOSMOnlyEHARef` (OSM-only buildings), `\vlmExact`, `\vlmWithin`,
`\vlmBaseExact` (two-pass VLM on the depth benchmark). Rerun `make_numbers.py` when those runs finish; the text
around them was written for the expected direction of the finding and should be re-read once the numbers land.

## Before submission

- Authorship: sole author in the draft; decide whether the supervisor or others should be added.
- The depth benchmark labels are AI-assisted with a blind human review pending; update Threats to Validity when
  the review lands.
- Venue page limits and anonymisation are not applied.

## References

27 entries in `references.bib`, each checked against the publisher, arXiv or the project page.
