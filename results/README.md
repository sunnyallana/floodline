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

`../data/depth_labels.csv` · 300 flooded images from the CVFD **test** split, hand-labelled into
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

## 5. Supervised baselines carried over from v1

`../legacy/v1-results/` · seven backbones, three seeds, binary flooded / not-flooded

| Model | CVFD test accuracy | AlleyFloodNet (OOD) accuracy |
|---|---|---|
| DINOv3 ViT-S (fine-tuned) | 98.84% | 74.1% ± 1.2 |

The 25-point out-of-distribution drop is the reason v2 does not train a classifier.

## Reproduce

```bash
floodline index && floodline sheets --split test && floodline depth && floodline segment
floodline waterline
floodline assess --model Qwen3VL-8B-Instruct-Q8_0.gguf --mmproj mmproj-Qwen3VL-8B-Instruct-F16.gguf
```
