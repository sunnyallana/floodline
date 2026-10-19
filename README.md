# Floodline

> Flood depth and impact from ground-level photos, zero-shot, fused with satellite flood extent.
> The depth method is the *waterline*: where the water meets an object of known height.

Ground-level flood photos from field responders and connected residents as evidence: estimate **flood depth and human impact zero-shot**, locate each
report with a stated confidence, and fuse the result with **satellite flood extent** to prioritise rescue.

Status: active development (October 2026). Successor to the GroundTruth final-year project. Nothing below is a claim until its result file exists under `results/`.

## Scope: who takes the photos, and when

People surrounded by water do not take photos, and networks degrade. The design accepts both facts.

- **Sources.** Field responders (Rescue 1122, army units, NGO teams, who document for handovers and boat
  requests) and residents on upper floors or at the edge of the flooded zone, where towers usually survive.
  Not people in danger reporting themselves.
- **Connectivity.** Store-and-forward: photos are captured offline, keep GPS and timestamp in the file, and
  upload when signal returns. The DINOv3 offline path scores them on the phone with no network at all.
- **Coverage bias.** The worst-hit hexes will have the fewest photos. Satellite extent is the coverage
  backbone; a hex without photos keeps a satellite-only estimate with lower confidence, and the
  photo-vs-satellite agreement score is partly a measure of this bias.
- **User.** A district flood coordinator deciding where boats and relief go first, at the flood peak (triage
  from responder photos) and in the recession phase (damage assessment and relief allocation).
- **Data reality.** CVFD is web-sourced imagery (FloodIMG CC0, scraped non-flood scenes, 343 own captures),
  resized to 224 px with EXIF removed. It has no GPS and is used as a benchmark, not as production data.

## Why a rebuild

The v1 pipeline (binary flooded / non-flooded, 224 px crops, supervised backbones) scores 98.84% on its own
test split and **74.1%** on AlleyFloodNet. It learned the dataset, not floods. v2 does not train a classifier.

## Pipeline

| Stage | Method | Role of v1 assets |
|---|---|---|
| Segmentation | SAM 3 concept prompts: water, person, car, motorbike, door, window, rooftop | the 2,365 / 605 / 1,109 CVFD masks become the **benchmark** (IoU), not training data |
| Depth | waterline against reference objects of known height + metric monocular depth | 300-image depth-bin benchmark labelled from CVFD |
| Impact | open-weight VLM with a strict JSON schema (people, roofs, vehicles, flow, signs, depth class, confidence) | DINOv3 kept as the cheap offline screen; compared on accuracy, latency, memory |
| Location | tier 1 EXIF GPS · tier 2 text and landmarks read from the image, geocoded · tier 3 report text or district | CVFD has no GPS; mapping uses geotagged external imagery and live capture |
| Satellite | Sentinel-1 water mask (Prithvi-EO 2.0 flood head) with Copernicus EMS polygons as reference | |
| Fusion | H3 hexagons: depth distribution, people, satellite water fraction, agreement/conflict, road reachability (OSM) | |
| Serving | GeoParquet + PMTiles + MapLibre; QGIS project; ONNX offline path | |

## Layout

```
floodline/
  pyproject.toml           package metadata, entry point `floodline`, ruff and pytest config
  src/floodline/
    config.py              paths, model ids, prompts: the only place these are defined
    cvfd.py                dataset index (one row per image, mask paths)
    segeval.py             IoU / Dice / boundary-F1
    depth.py               flood depth from reference objects of known height
    schema.py              PhotoAssessment (the VLM contract) and the extraction prompt
    llama.py               llama-server as a context manager; one JSON-constrained chat call
    resume.py              append-only JSONL / CSV stores so long runs survive restarts
    runs.py                run manifests: git sha, params, metrics, timing, next to every result
    cli.py                 `floodline index|sheets|depth|segment|overlay|assess`
    stages/                one module per stage, each with a resumable `run()`
  tests/                   pure-logic tests; no model downloads, run in CI on 3.11 and 3.12
  data/                    derived indexes and labels (small); raw CVFD under data/cvfd (see data/README.md)
  legacy/v1-results/       aggregate metrics of the supervised v1 baselines, quoted in the write-up
  results/                 metrics, manifests, previews (committed); masks and depth maps (ignored)
  models/, tools/          local GGUF weights and llama.cpp binaries (ignored)
```

## Running

```bash
pip install -e ".[dev]"            # base + tests;  add [models] for SAM 3 / depth,  [geo] for fusion
floodline index                  # data/cvfd_index.parquet + results/cvfd_summary.csv
floodline sheets                 # 300-image depth benchmark contact sheets
floodline depth                  # Depth Anything V2 metric depth maps (CPU, ~3 s/image)
floodline segment                # SAM 3 concept segmentation, scored vs CVFD masks (CPU, ~18 s/call)
floodline assess --limit 20      # VLM structured assessment via llama-server (CPU)
floodline overlay flooded_0003   # original | SAM 3 | CVFD mask preview
```

Every stage writes `results/<stage>_run.json` with the git sha, model ids, parameters and timing. Long stages
are resumable: rerun the same command and it continues from the last record.

## Honesty rules for this repo

- Every number in the README or CV points to a file in `results/`.
- Locations carry a tier and a confidence. No coordinates are invented or inferred beyond the stated tier.
- CVFD is released under a research-use, non-commercial licence with a takedown policy, because the sources
  of the video frames were not recorded. The datasheet says so.
