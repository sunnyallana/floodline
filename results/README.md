# Results

Every number here points to a file in this directory, and every run file carries the git sha, model ids,
parameters and timing of the run that produced it. Interim figures are marked as such and replaced as runs
complete.

## 1. Zero-shot segmentation vs. the CVFD masks (interim)

`sam3_scores.csv`, `sam3_run.json` · SAM 3 (`facebook/sam3`), concept prompts, CPU · flooded test images

| Prompt → CVFD mask | Mean IoU | Median IoU | Mean boundary F1 | n |
|---|---|---|---|---|
| water → flood | 0.959 | 0.988 | 0.957 | 167 |
| person → humans | 0.868 | 0.937 | 0.940 | 43 |
| building → buildings | 0.755 | 0.944 | 0.805 | 81 |

First 180 of 538 images. The CVFD masks were produced SAM 3-assisted with manual review, so this partly measures
SAM 3 against itself; the medians near 0.99 are that circularity, and the gap between mean and median is the set
of images where a reviewer changed the mask (mostly aerials and building outlines). The independent checks are
the depth benchmark and the VLM comparison below.

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

## 3. Waterline geometry (interim)

`waterline_depth.csv`, `waterline_run.json` · SAM 3 instance masks + Depth Anything V2 metric depth, no language model

| Version | Exact | Within one bin | Mean error (bins) | n |
|---|---|---|---|---|
| v0: submerged fraction read from the mask | 0.13 | 0.47 | 1.8 | 15 |
| v1: visible-fraction stature + depth-consistent touch | 0.29 | 0.79 | 1.0 | 14 |

Why v0 failed, from the debug overlays (`waterline_debug.png`): a mask only covers the part of an object above the
surface, so its bottom edge *is* the waterline and "fraction below water" is zero by construction; and water visible
behind an object sits higher in the image than its base, which v0 read as submersion. v1 infers depth from how much
of the object's height is visible (stature from shoulder width, door height from width) and only accepts water that
touches the object's base at the object's distance in the depth map. Numbers will be re-run on the full overlap when
segmentation completes.

## 4. Vision-language assessment (smoke tests, 6 images)

`vlm_cmp_qwen25vl.jsonl`, `vlm_cmp_qwen3vl_2pass.jsonl`, `vlm_run_*.json` · llama.cpp, CPU, grammar-constrained JSON

| Design | Model | Depth class from the model | Note |
|---|---|---|---|
| single pass, model emits depth class | Qwen2.5-VL-7B Q8 | `unknown` on 6/6 | description correct ("water up to their knees"), field not filled |
| single pass | Qwen3-VL-8B Q8 | `unknown` on 4/4 | better descriptions and calibrated confidence, same failure |
| two pass: describe, then read (rules → LLM fallback) | Qwen3-VL-8B Q8 | 3 of 6 classified, 3 correctly `unknown` (2 aerial, 1 no reference) | median 71 s/image on 6 threads |

Finding: both VLMs describe the waterline correctly in prose and fail to map it to an enumerated field in the same
call. Splitting the task fixes it: pass 1 describes, pass 2 maps the description to (reference object, water level)
by deterministic phrase rules with an LLM text-only fallback, and the depth class is computed in code. The full
538-image run is scheduled after segmentation finishes.

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

## 7. Supervised baselines carried over from v1

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
