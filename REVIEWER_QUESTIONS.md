# Questions a reviewer will ask, and where the answer is

Each answer points at the file that settles it. The numbers are copied from those files on 20 October 2026; if a
file and this page disagree, the file is right.

### Does the ranking find the places that were actually hit?

On six floods on four continents, measured against Copernicus EMS analysts' building-by-building damage
grading, the top 10 % of hexagons hold 0.76 of the damaged or destroyed buildings (95 % interval 0.55 to 0.78;
random is 0.10). That is 11 towns and 3,806 buildings. The grading is done from post-event imagery and never sees
Floodline's inputs or outputs. `results/benchmark_summary.json`, `results/README.md` section 8.

### Isn't that just "count buildings in the water"?

Pooled, building counts score the same (0.77). The difference is on the worst flood. Building counts fall to
0.07 in Moramanga, Madagascar, and water extent alone falls to 0.00 in New South Wales. Every single input layer
fails on at least one flood. The fused priority's worst flood is 0.37. A coordinator does not know in advance
which kind of flood this is, so robustness is the property that matters. `ablation_per_event` in the summary.

### Why not learn the weights from the data?

That was tried: weights were fitted on five floods and tested on the sixth, for every choice of held-out flood.
The fitted rule averages 0.59 held out; the fixed default averages 0.72. Fitted rules collapse on Moramanga
(0.10) and Sanghar (0.23). Fitting overfits six floods. `leave_one_event_out` in the summary.

### Access (travel time, cut-off areas) doesn't improve damage capture. Why is it in the score?

It doesn't, and the page says so. With access, capture is 0.016 lower than without, and the 95 % interval
(−0.071 to +0.005) includes zero. Access is there because "can a truck get there?" is the other half of the
coordinator's question, and damage grading cannot measure that half. Closed roads and cut-off areas are also drawn
as their own layer. The road-closure rule itself is validated against the analysts' road grading: precision
0.94, recall 0.85 at the 5 m overlap used (`results/benchmark_roads.csv`).

### How do you stop bogus reports from pulling responders to the wrong place?

A field report's depth or people claim raises priority only when something independent backs it: satellite water
near the report, the model's reading of the photo, or, where the satellite has not mapped the area, a second
device. Beyond that:
- known photos are rejected by near-duplicate hashing against 11,563 flood images, which catches 99.8 % of
  forwarded copies;
- uploads are rate-limited per device and per team;
- capture times are bounded;
- the database is insert-only.

Simulated attackers with a valid team code and photos that pass every check:

| Attack on the Prague map | Top 10 unchanged, permissive rule | Top 10 unchanged, rule used | Flagged |
|---|---|---|---|
| 50 lone fakes in dry areas | 1.00 | 1.00 | 50 / 50 |
| 10 colluding phone pairs in dry areas | 0.30 | 1.00 | 10 / 10 |
| 50 exaggerated fakes inside mapped water | 0.39 | 0.91 | 50 / 50 |

`THREAT_MODEL.md` (including the gaps it does not close), `results/attack_prague*.json`,
`results/trust_eval.json`.

### Does it need analysts' maps, which arrive a day or more late?

The whole benchmark is rerun with Sentinel-1 radar in place of the EMS map, in two ways: change detection
against a pre-event scene, and a single threshold. Results are in `data_ablations` in the summary.

Two physical failures of change detection are reported, not hidden:
- it misses water that was already there before the pre-event scene, as in Sanghar's months-long monsoon flood;
- it misses floods that had drained before the satellite passed, as in Erftstadt.

In Larkana the radar map agrees with EMS at IoU 0.74 (`results/sentinel1_larkana_run.json`).

### OpenStreetMap is incomplete in the places that flood most.

In Larkana, OSM had 108 buildings where Microsoft's footprints have 78,848. The pipeline takes the larger of the
two per hexagon by default. The benchmark is also rerun with OSM buildings only (`data_ablations`), so the cost of
relying on OSM is measured rather than assumed.

### Does the photo model work?

Partly. SAM 3 water masks match the dataset masks at mean IoU 0.96. That is partly circular, since those masks
were made with SAM 3 assistance. The waterline depth method did not beat the "always the commonest class"
baseline (40 % exact vs 38 %; 78 % within one class vs 83 %). It is reported as a negative result. The
vision-language model is being scored on the same benchmark against the same baseline, written to
`results/vlm_benchmark.json` when its run completes.

### Are the depth labels trustworthy?

Not yet. They were assigned with an AI assistant. A blind human review tool is at
https://floodline.sunnyshabanali.dev/review/, and `floodline labels` computes Cohen's kappa against the first
pass. The dataset card says this.

### What is not shown?

- Building damage is a proxy for need. It does not see people stranded on roofs.
- EMS graded the towns its analysts looked at, not a random sample.
- OSM and the footprints are today's maps, not the maps on the day of each flood.
- No coordinator has used Floodline in a live flood. `paper/practitioner_review.md` is the questionnaire for
  rescue and disaster-management staff.

### Can I reproduce it?

Yes. The OSM extracts are pinned by URL and md5 (`data/README.md`). Dependencies are locked
(`requirements/lock-geo-dev.txt`). There is a container, and every result file carries the git SHA, parameters
and host that produced it. `scripts/reproduce_geo.sh`; `floodline benchmark ...` then
`floodline benchmark summary`.
