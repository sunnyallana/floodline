# Results

Every number here points to a file in this directory, and every run file carries the git sha, model ids,
parameters and timing of the run that produced it. Interim figures are marked as such and replaced as runs
complete.

## 1. Zero-shot segmentation vs. the CVFD masks

`sam3_scores.csv`, `sam3_run.json` · SAM 3 (`facebook/sam3`), concept prompts, CPU · all 538 flooded evaluation
images, 686 prompt calls with a CVFD mask to score against

| Prompt → CVFD mask | Mean IoU | Median IoU | Mean boundary F1 | n |
|---|---|---|---|---|
| water → flood | 0.959 | 0.989 | 0.960 | 482 |
| person → humans | 0.882 | 0.945 | 0.951 | 138 |
| building → buildings | 0.775 | 0.948 | 0.814 | 229 |

The CVFD masks were produced SAM 3-assisted with manual review, so this partly measures SAM 3 against itself; the
medians near 0.99 are that circularity, and the gap between mean and median is the set of images where a reviewer
changed the mask (mostly aerials and building outlines). The independent checks are the depth benchmark below and
the damage-grading validation in section 7.

## 2. Depth benchmark

`../data/depth_labels.csv` · 300 flooded images from the CVFD **test** split, labelled from contact sheets with an AI
assistant (not yet reviewed by a second, human annotator) into
dry / ankle / knee / waist / chest / above_head / unknown, with the reference object and a note.

| Class | Images |
|---|---|
| unknown (aerial, river, no reference object) | 203 |
| knee | 29 |
| ankle | 27 |
| dry | 16 |
| waist | 10 |
| chest | 9 |
| above_head | 6 |

Two thirds of web-sourced "flood" imagery has no object in the water that could calibrate depth. That is a result
about the data, and it is why the production design treats satellite extent as the coverage backbone and photos as
depth evidence where they exist. 42 of the 97 labelled images carry a "low confidence" note.

## 3. Waterline geometry

`waterline_depth.csv`, `waterline_run.json` · SAM 3 instance masks + Depth Anything V2 metric depth, no language model

| Version | Exact | Within one bin | Mean error (bins) | n |
|---|---|---|---|---|
| v0: submerged fraction read from the mask | 0.13 | 0.47 | 1.8 | 15 |
| v1: visible-fraction stature + depth-consistent touch (interim) | 0.29 | 0.79 | 1.0 | 14 |
| v1, all 538 images (estimate for 135; 40 in the benchmark) | 0.40 | 0.78 | 0.85 | 40 |
| baseline: always "ankle", the commonest class on the same 40 | 0.38 | 0.83 | | 40 |

The geometric estimate is no better than guessing the commonest class on this sample. Its errors are not random:
it recognises dry scenes (10 of 11), calls ankle-deep water knee-deep (7 of 15), and never predicts above head. 40
images is small and the labels are AI-assisted (section 2), so this is a weak result in both directions. What it
does show is that geometry alone should not decide depth; model-assessed depth corroborates a report only through
the rules in `fuse.score`, and section 4 tests the language-model path on the same benchmark.

Why v0 failed, from the debug overlays (`waterline_debug.png`): a mask only covers the part of an object above the
surface, so its bottom edge *is* the waterline and "fraction below water" is zero by construction; and water visible
behind an object sits higher in the image than its base, which v0 read as submersion. v1 infers depth from how much
of the object's height is visible (stature from shoulder width, door height from width) and only accepts water that
touches the object's base at the object's distance in the depth map.

## 4. Vision-language assessment

`vlm_cmp_qwen25vl.jsonl`, `vlm_cmp_qwen3vl_2pass.jsonl`, `vlm_run_*.json` · llama.cpp, CPU, grammar-constrained JSON

| Design | Model | Depth class from the model | Note |
|---|---|---|---|
| single pass, model emits depth class | Qwen2.5-VL-7B Q8 | `unknown` on 6/6 | description correct ("water up to their knees"), field not filled |
| single pass | Qwen3-VL-8B Q8 | `unknown` on 4/4 | better descriptions and calibrated confidence, same failure |
| two pass: describe, then read (rules → LLM fallback) | Qwen3-VL-8B Q8 | 3 of 6 classified, 3 correctly `unknown` (2 aerial, 1 no reference) | median 71 s/image on 6 threads |

Finding: both VLMs describe the waterline correctly in prose and fail to map it to an enumerated field in the same
call. Splitting the task fixes it: pass 1 describes, pass 2 maps the description to (reference object, water level)
by deterministic phrase rules with an LLM text-only fallback, and the depth class is computed in code.

**Full run, all 538 images** (`vlm_assessments.jsonl`, `vlm_run_Qwen3VL-8B-Instruct-Q8_0.json`,
`vlm_benchmark.json`). Qwen3-VL-8B Q8 on CPU: 537 valid outputs (0.19 % failures), median 59 s per image. Pass 2
was settled by the phrase rules on 339 images and needed the text-only fallback on 46. The model answered
`unknown` on 251 images, and on 23 of the 97 labelled benchmark images.

| On the 74 labelled images it classified | Exact | Within one class |
|---|---|---|
| Qwen3-VL-8B, two pass | 0.30 | 0.77 |
| always "ankle" (commonest true class on the same images) | 0.35 | 0.80 |

A negative result, like the waterline. The errors are one-sided: the model calls 36 of the 74 "knee", so shallow
water is mostly wrong (dry 1 of 11, ankle 2 of 26), while deep water is mostly right (chest 6 of 6, above head
2 of 3). Deep water is where depth matters most for rescue. The benchmark labels are AI-assisted and the shallow
classes are the hardest to label, so part of the shallow error may be label noise; the blind human review will
tell. The fusion therefore never lets a model-read depth raise priority on its own (`fuse.score`).

## 5. Satellite flood extent and photo ⇄ satellite agreement

`satellite_EMSR*.csv`, `satellite_run_EMSR*.json` · Copernicus EMS rapid-mapping delineation products, H3 res 8.
A hex inside a mapped area of interest gets a real value (0 = mapped dry); hexes outside every AOI are unknown.

| Activation | Event | Mapped area | Flooded | Hexes mapped | Hexes with water |
|---|---|---|---|---|---|
| EMSR044 | Elbe, Germany (Dresden, Meissen, Riesa), June 2013 | 421 km² | 21 km² | 620 | 200 |
| EMSR045 | Vltava/Elbe, Czechia (Prague, Kralupy, Mělník, Brandýs), June 2013 | 1,167 km² | 36 km² | 1,674 | 243 |
| EMSR046 | Danube, Hungary (Győr), June 2013 | 215 km² | 9 km² | 290 | 95 |
| EMSR517 | Western Germany, July 2021 | 5,209 km² | 34 km² | 7,946 | 759 |
| EMSR629 | Sindh, Pakistan (Larkana, Shikarpur, Jacobabad), Aug–Sep 2022 | 2,891 km² | 1,588 km² | 3,802 | 3,614 |

`agreement_euflood_run.json`, `agreement_euflood_photos.csv`, `agreement_euflood_distances.csv` · the 883 geotagged
European Flood 2013 photos against the 2013 layers. 442 photos fall inside a mapped area; the rest (including all 83
Győr photos, which share a single copied geotag) are outside and counted as unknown, not as disagreement.

Distances, per unique location (one point per geotag; batches sharing a copied geotag are counted once):

| City | Locations | Median to mapped flood | Median to river channel | Within 150 m of flood | Within 150 m of flood or river |
|---|---|---|---|---|---|
| Dresden | 171 | 142 m | 35 m | 53% | 92% |
| Prague | 252 | 3,102 m | 21 m | 22% | 88% |

Finding: in Dresden the photos sit at the edge of the mapped flood, as expected for people photographing a
flooded floodplain from dry ground. In Prague they sit on the river and kilometres from any mapped overflow: the
Vltava rose inside its embankments and mobile barriers in the centre, so the photos show a high river, not flooded
streets, and the satellite map is right to show no overflow there. "Water is visible in the photo" and "this place
is flooded" are different questions, which is why the satellite stage reports permanent water in its own column
and the fusion layer must not read a river photo as a flooded hex. Hex-level agreement alone cannot show this:
within one ring of a 0.7 km² hex, every photo in both cities touches either flood or river (100%).

## 6. Responder layer: roads, buildings and priority (Prague and Dresden, June 2013)

`satellite_{prague,dresden}.csv`, `reach_*`, `exposure_*`, `hexes_*` with their `_run.json` files · one EMS city
package each, H3 res 9 (0.1 km² hexagons). Published as the map in `../docs/`.

| | Prague | Dresden |
|---|---|---|
| Mapped area / flood (EMS map dated 3 June 2013) | 261 km² / 5.6 km² | 77 km² / 1.5 km² |
| Hexagons mapped / with priority > 0 | 2,612 / 219 | 796 / 71 |
| Drive-network nodes (OSM) | 16,935 | 4,950 |
| Staging points (OSM fire stations and hospitals, outside the flood) | 80 | 21 |
| Roads closed by water / length under water | 39 / 4.2 km | 4 / 0.3 km |
| Hexagons cut off by road | 10 | 0 |
| OSM buildings inside the flood | 116 | 10 |

How it is computed:
- **Reach.** Multi-source Dijkstra from the staging points. The drive network is run twice: once intact, once
  with every road that runs at least 5 m through the flood polygons removed. The threshold was chosen against
  EMS road damage grading (section 7): any overlap at all closes roads that only touch the polygon edge, and
  15 m misses closures.
- **Cut off.** A hexagon is cut off when it has a road, is reachable on the intact network, and is not
  reachable on the flooded one.
- **Priority.** It is zero unless the hexagon is wet or cut off. Otherwise it is
  0.45·exposure + 0.30·hazard + 0.25·access, where exposure is 1 − e^(−buildings/20).

Findings:
- **Prague.** The flood map puts the damage on the southern edge of the city, not the centre. All five
  top-ranked hexagons are cut off: Zbraslav, Lipence, Lahovice and Velká Chuchle, at the Berounka–Vltava
  confluence. The highest-ranked one has 45 buildings affected and no road in. The 203 geotagged photos cluster
  along the embankments in the centre, kilometres north of those hexagons. This is the coverage bias the scope
  section predicts: the people taking photos were not where the need was.
- **Dresden.** The EMS map is dated 3 June, three days before the Elbe crested on 6 June. It shows riverside
  meadows under water and almost nothing else: 10 buildings, no hexagon cut off. Its top priority is 0.20.
  The low score is the correct reading of that map. It does not show the flood's peak.
- **Caveat.** The OSM network and buildings are the current map, not the 2013 one, and whether a bridge was
  closed is not in the data. Photos raise confidence, not priority, until their depth has been assessed.

## 7. Validation against EMS damage grading (western Germany, July 2021)

`validation_roads.csv`, `validation_run.json`, `validation_ranking.json`, `validation/` · EMSR517 grading products:
analysts' damage grades for building points and road segments, from post-event imagery. They do not use
OpenStreetMap, our routing or our priority. The one shared input is the product's flood polygons, which we use
as the flood extent. OSM for the ranking runs comes from Geofabrik extracts dated 18 October 2026
(`floodline validate --osm-pbf`); on Erftstadt the extract and Overpass gave the same closed roads (130),
staging points (12), road hexagons (213) and cut-off hexagons (26).

**Road closure rule** (11 towns, roads graded anything but "Not Analysed"; target = possibly damaged, damaged or
destroyed):

| Overlap needed to close a road | Precision | Recall | F1 |
|---|---|---|---|
| any (0 m) | 0.82 | 0.88 | 0.85 |
| 5 m (used) | 0.94 | 0.85 | 0.89 |
| 15 m | 0.95 | 0.76 | 0.84 |
| 50 m | 0.95 | 0.50 | 0.65 |

Against "damaged or destroyed" only, recall at 5 m is 0.92 and precision 0.41: most roads under water were
impassable without being visibly damaged, which is what a closure rule should capture.

**Priority against building damage** (the four towns with at least 20 damaged or destroyed buildings; share of
those buildings inside the top-ranked hexagons):

| Ranking | Top 5 % | Top 10 % | Top 20 % | Spearman |
|---|---|---|---|---|
| random | 0.05 | 0.10 | 0.20 | |
| satellite water fraction alone | 0.21 | 0.63 | 0.92 | 0.25 |
| OSM buildings inside the flood alone | 0.72 | 0.78 | 0.78 | 0.47 |
| priority | 0.66 | 0.75 | 0.91 | 0.27 |

Pooled over 1,143 damaged or destroyed buildings. Per town, priority's top-10 % capture is 0.78 (Erftstadt),
0.69 (Eschweiler), 0.93 (Wassenberg) and 0.56 (Bad Neuenahr-Ahrweiler).

Reading: priority is far better than water extent alone at the top of the list and keeps finding damage deeper
down, where the buildings-only score stops (most flooded hexagons have no OSM building inside the flood polygon,
so that score ties at zero). Buildings-only edges priority at the very top because priority also ranks hexagons
that are cut off but not damaged, by design: damage is a proxy for need, not the same thing.

**Weights.** Across 125 weight combinations (each weight x0.5 to x1.5), top-10 % damage capture moves by up to
15 points per town, but the best setting differs by town (Erftstadt prefers exposure-heavy, Ahrweiler
hazard-heavy). The defaults sit at the median of each town's range, so there is no evidence for retuning, and
tuning on this data would be circular. On Prague the full ordering is stable (Spearman >= 0.98 for every
variant); 86 % of variants keep at least 8 of the top 10 hexagons, and the ones that do not all make hazard
dominant, which pulls fully flooded open land to the top (`sensitivity_*.csv`, `sensitivity_run.json`,
`share_keeping_8_of_top10`).

## 8. Six floods, four continents: the multi-event benchmark

`benchmark_summary.json`, `benchmark_towns.csv`, `benchmark_roads.csv`, `benchmark/<event>/` · `floodline
benchmark <EMSR> <grading dir> --osm-pbf <extract>` per event, then `floodline benchmark summary --bootstrap 1000`.
Every town in six EMS grading activations with at least 20 damaged or destroyed buildings, run under one
protocol. The flood extent is the product's own polygons, buildings are the larger of OSM and Microsoft
footprints per hexagon, population is GHSL 2020, and the OSM extracts are pinned in `data/README.md`.
11 towns, 10,886 hexagons and 3,806 damaged or destroyed buildings.

| Event | Towns | Severe buildings |
|---|---|---|
| EMSR517 western Germany, Jul 2021 (flash) | Erftstadt, Eschweiler, Wassenberg, Bad Neuenahr-Ahrweiler | 1,143 |
| EMSR631 Sanghar, Pakistan, Sep 2022 (riverine) | Sanghar | 2,008 |
| EMSR504 New South Wales, Australia, Mar 2021 (riverine) | Kempsey | 78 |
| EMSR559 Madagascar, Jan 2022 | Moramanga | 69 |
| EMSR634 Marche, Italy, Sep 2022 (flash) | Cantiano, Ostra, Senigallia | 474 |
| EMSR649 Sicily, Feb 2023 (flash) | Pachino | 34 |

Metric: share of a town's damaged or destroyed buildings inside its top 10 % of hexagons, averaged over towns
(pooled) or over the towns of one event. Random scores 0.10. The intervals come from a spatial block bootstrap
(H3 resolution-6 blocks resampled within towns, 1,000 draws).

| Score | Pooled [95 % CI] | DE | PK | AU | MG | IT-Marche | IT-Sicily | Mean of events | Worst event |
|---|---|---|---|---|---|---|---|---|---|
| **fused priority (default)** | **0.76** [0.55, 0.78] | 0.74 | 0.37 | 0.41 | 0.86 | 0.95 | 0.97 | **0.72** | **0.37** |
| fused, without access | 0.78 [0.58, 0.80] | 0.76 | 0.44 | 0.40 | 0.87 | 0.95 | 0.97 | 0.73 | 0.40 |
| risk: exposure x (0.5 + 0.5 hazard) | 0.77 [0.61, 0.80] | 0.84 | 0.44 | 0.81 | 0.10 | 0.93 | 0.94 | 0.68 | 0.10 |
| buildings in the flood | 0.77 [0.63, 0.80] | 0.85 | 0.41 | 0.83 | 0.07 | 0.93 | 0.94 | 0.67 | 0.07 |
| people in the flood (GHSL) | 0.70 [0.55, 0.76] | 0.82 | 0.22 | 0.58 | 0.33 | 0.87 | 0.65 | 0.58 | 0.22 |
| exposure alone | 0.67 [0.57, 0.73] | 0.84 | 0.09 | 0.73 | 0.10 | 0.74 | 0.94 | 0.57 | 0.09 |
| water extent alone | 0.56 [0.41, 0.64] | 0.39 | 0.02 | 0.00 | 0.88 | 0.90 | 0.97 | 0.53 | 0.00 |
| access alone | 0.33 [0.15, 0.36] | 0.23 | 0.00 | 0.35 | 0.03 | 0.62 | 0.47 | 0.28 | 0.00 |

Paired differences (default minus the other, same bootstrap draws): water +0.20 [0.02, 0.29], buildings −0.00
[−0.17, 0.03], people +0.07 [−0.11, 0.14], without access −0.02 [−0.07, 0.01].

**Roads.** The closure rule against the analysts' road grading, pooled over all six events (target: possibly
damaged, damaged or destroyed; `benchmark_roads.csv`):

| Overlap needed to close a road | Precision | Recall | F1 |
|---|---|---|---|
| any (0 m) | 0.68 | 0.80 | 0.74 |
| 5 m (used) | 0.77 | 0.77 | 0.77 |
| 15 m | 0.78 | 0.70 | 0.74 |
| 50 m | 0.79 | 0.48 | 0.60 |

5 m, chosen on Germany alone (section 7), is still the best F1 out of sample.

**Worse inputs** (`data_ablations`; the same 11 towns rerun into `benchmark_s1/`, `benchmark_s1otsu/` and
`benchmark_osm/`):

| Rerun | Fused priority, top-10 % capture | Median share of a town called water |
|---|---|---|
| default: EMS map, larger of OSM and Microsoft buildings | 0.76 | 8.0 % (EMS) |
| Sentinel-1 change detection instead of the EMS map | 0.21 | 0.4 % |
| Sentinel-1 single threshold (Otsu) instead of the EMS map | 0.27 | 47 % |
| OpenStreetMap buildings only | 0.74 | 8.0 % |

Radar alone does not yet support ranking within towns, and its two forms fail in opposite directions. Change
detection finds almost no water: Sanghar's pre-event scene (30 August 2022) was already flooded, and the low shares in the
flash-flood towns are consistent with water that had drained by the overpass. A single threshold calls nearly
half of each town water, because built-up areas and wet ground return like water at 10 m. On Larkana's open
floodplain the radar map does match EMS (section 10, IoU 0.74). OSM-only buildings barely move the pooled number,
because the European towns are well mapped, but New South Wales falls from 0.41 to 0.19 and Sanghar from 0.37 to
0.23. That is where the Microsoft footprints earn their place.

**Leave one event out.** The scoring rule (additive weights on a 0.05 simplex, or the risk form with an access
weight; buildings or people as exposure) is fitted on five events and scored on the sixth. Mean held-out capture:
default 0.715, buildings only 0.672, fitted rule 0.591, people only 0.578. The fitted rule fails on Moramanga
(0.10) and Sanghar (0.23). Every fit chose an access weight of 0.

Reading:
- Pooled, the default is level with the best single layers; the intervals overlap.
- What separates the scores is the worst event. Every building, water, exposure or access layer falls to 0.10 or below on at least one flood
  (population alone to 0.22): water extent
  where the whole plain is wet (NSW, Sanghar), building counts in Moramanga, where the footprints under the
  water are not where the damage was graded. The fused priority's worst event (0.37) is the best worst case, and
  it has the best held-out mean.
- Fitting weights on these data overfits. The default weights are a prior, not a fit, and they transfer better.
- Access does not help find damage; its interval includes zero. It stays in the score because reachability is the
  other half of a coordinator's question, and closed roads and cut-off areas are drawn as their own layer. Damage
  grading cannot measure that half.
- Ostra's fire stations and hospitals all lie outside the downloaded road network. Each enters at its nearest
  road node, the gap costed at 30 km/h (`reach.add_virtual_source`).

## 9. Report trust and simulated attacks

`trust_eval.json` · 64-bit difference hash against 11,563 CVFD and European Flood 2013 images.

| Check | Result |
|---|---|
| forwarded copies (half size, JPEG quality 45) caught | 99.8 % of 500 |
| 10 % centre crops caught | 21.8 % of 500 |
| unrelated photos landing in the 3-6 bit review band | at most 0.7 % (27 of 3,710) |

Found on the way: 2,371 of 3,710 European Flood 2013 photos are also in CVFD, and 20 of 1,180 CVFD test images
have a near-copy in training (`data/DATASET_CARD.md`).

`attack_prague.json` · synthetic reports injected into the Prague ranking, 20 draws each, attacker assumed to
have a valid team code and photos that pass every trust check:

| Scenario | Top-10 overlap with clean | Fake hexagons flagged for verification |
|---|---|---|
| 50 lone reports, chest-deep and 5 people, in mapped-dry hexagons | 1.00 | 50 of 50 |
| 10 pairs of colluding devices in mapped-dry hexagons | 1.00 | 10 of 10 |
| 50 lone reports inside satellite-mapped water | 0.91 | 50 of 50 |

The first version of the rule let two agreeing devices corroborate each other anywhere, and let satellite water
confirm people. Replayed as an ablation (`floodline attack --permissive`, `attack_prague_permissive.json`): 7 of 10
colluding pairs reached the top ten (top-10 overlap 0.30), and 50 lone reports inside mapped water put on average
8.35 of their hexagons there (overlap 0.39), none of them flagged. Both are closed: device agreement no longer overrules a satellite map that says dry, and satellite water
confirms depth but never people. What remains is the third row: inside mapped water, a fabricated depth claim
counts, which displaced on average one of the top ten.

## 10. Larkana, Sindh, 2022, and an independent radar check

`satellite_larkana.csv`, `reach_larkana*`, `exposure_larkana*`, `hexes_larkana*` · EMSR629 AOI01 (Larkana), flood
mapped from SPOT-6/7 imagery of 30 August 2022, H3 res 9. OSM from the Geofabrik Pakistan extract of 19 October
2026. Published on the map.

| | Larkana |
|---|---|
| Mapped area / flood | 964 km² / 502 km² |
| Hexagons mapped / with priority > 0 | 8,926 / 7,619 |
| Roads closed by water / length under water | 2,320 / 365 km |
| Hexagons cut off by road | 678 |
| Buildings inside the flood, in mapped hexagons | 10,175 (the exposure run counts 10,180; 5 sit in 4 hexagons just outside the mapped area) |

**Buildings.** OpenStreetMap holds 108 buildings in the 7,663 hexagons that matter here; Microsoft's Global ML
Building Footprints (release 2026-02-03) hold 78,848. Exposure uses the larger of the two counts per hexagon
(`floodline exposure --buildings max`), which fills the gap without counting twice a house both sources map.
With OSM alone, exposure in rural Sindh would be close to zero and the ranking would rest on water and roads.
Europe keeps OSM-only counts, where OSM is complete and every validated number above was computed.

**Top of the list.** The five highest-ranked hexagons are all cut off by road, with 55 to 85 buildings affected
each: four around Khokhar and one at Bhai Khan Waggan, Larkana district.

**Sentinel-1 against EMS** (`sentinel1_larkana.csv`, `sentinel1_larkana_run.json`). One radiometrically
terrain-corrected Sentinel-1A scene from the Planetary Computer, acquired 30 August 2022 at 01:26 UTC, the same
day as the SPOT image; VV at 20 m, water below an Otsu threshold of -12.9 dB, EMS permanent water removed from
both masks.

| Measure | Value |
|---|---|
| IoU, radar water vs EMS flood polygons | 0.74 |
| precision / recall of radar water | 0.94 / 0.78 |
| water share of the mapped area, radar / EMS | 0.48 / 0.59 |
| Spearman of per-hexagon water fraction (8,337 hexagons) | 0.94 |

Radar agrees with the analysts' map where water is open and finds less of it overall, as expected: shallow water
under crops and water between buildings return more signal than open water. Sentinel-1 was not in orbit in
2013, so the Prague and Dresden layers have no radar check.

## 11. Supervised baselines carried over from v1

`../legacy/v1-results/` · seven backbones, three seeds, binary flooded / not-flooded

| Model | CVFD test accuracy | AlleyFloodNet (OOD) accuracy |
|---|---|---|
| DINOv3 ViT-S (fine-tuned) | 98.84% | 74.1% ± 1.2 |

The 25-point out-of-distribution drop is the reason v2 does not train a classifier.

## Reproduce

```bash
floodline index && floodline sheets --split test && floodline depth && floodline segment
floodline waterline
floodline external
floodline satellite data/external/ems/EMSR045 --out satellite_EMSR045.csv
floodline agree results/eu_flood_2013_mappable.csv results/satellite_EMSR04{4,5,6}.csv \
    --ems-dirs data/external/ems/EMSR04{4,5,6} --out agreement_euflood
floodline assess --model Qwen3VL-8B-Instruct-Q8_0.gguf --mmproj mmproj-Qwen3VL-8B-Instruct-F16.gguf
```
