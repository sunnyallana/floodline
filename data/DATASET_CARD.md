---
license: cc-by-sa-4.0
task_categories: [image-classification, image-segmentation]
tags: [flood, disaster-response, remote-sensing-complement, benchmark]
pretty_name: Close-View Flood Dataset (CVFD)
size_categories: [1K<n<10K]
---

# Close-View Flood Dataset (CVFD)

Ground-level flood imagery for classification (flooded / non-flooded) and semantic segmentation (flood water,
humans, buildings), with a 300-image hand-labelled **flood-depth benchmark** added in Floodline v2.

- Images: 7,774 on disk (7,855 at first release; 81 files were lost from LFS), 224×224 RGB, EXIF removed.
- Splits: train / val / test ≈ 70 / 15 / 15, pre-sorted by class.
- Masks (flooded images only): flood water 3,287 · humans 865 · buildings 1,567, as single-channel PNGs.
- Depth benchmark: `depth_labels.csv`, 300 flooded test-split images, classes dry / ankle / knee / waist /
  chest / above_head / unknown, with reference object and a free-text note.

## Provenance

| Source | Share | Licence | Notes |
|---|---|---|---|
| FloodIMG (Karanjit, Pally & Samadi, *Data in Brief* 2023; Kaggle `hhrclemson/flooding-image-dataset`) | most flooded scenes | CC0 | drone and phone captures of floods worldwide; no per-image location in the release |
| Web search results | non-flood scenes (pools, rivers, beaches) and some Pakistani environments | unknown; sources not recorded | takedown on request, see below |
| Self-collected | 343 non-flooded images | CC BY-SA 4.0 | Islamabad, Mahodand Lake, Kashmir, Pir Chinasi, Swat, Shogran, Kalam, Karachi; captured by Muhammad Tulaib Tauseef |

All images were manually sorted into their class, faces and licence plates were blurred, overlaid timestamps and
text were removed, and duplicates and corrupt files were dropped before release.

## What the data is, and is not

- It is **web-sourced, worldwide** flood imagery resized to 224 px. It is not a set of citizen reports from one
  event, and it carries **no geolocation**: the resize step stripped EXIF and the originals were not kept.
- Two thirds of the flooded test images show no object standing in the water that could calibrate depth (aerials,
  rivers in flood, water with no people, vehicles or doors in it). The depth benchmark records these as `unknown`.
- Masks were made with SAM 3 assistance and manual review. Scoring a SAM 3-based method against them is therefore
  partly circular; use the depth benchmark and external sets for independent evaluation.

## Intended use

Benchmarking zero-shot segmentation and flood-depth estimation from ground-level photos; training small
classifiers for offline screening. Not suitable for inferring where a flood happened.

## Takedown

The web-scraped portion was collected without recording source URLs. If an image is yours and you want it removed,
open an issue at https://github.com/sunnyallana/floodline with the image id and it will be removed from the next
release.

## Citation

```
@dataset{shabanali2026cvfd,
  author = {Shaban Ali, Sunny},
  title  = {Close-View Flood Dataset (CVFD)},
  year   = {2026},
  url    = {https://www.kaggle.com/datasets/sunnyshabanali/close-view-flood-dataset-cvfd}
}
```
Also cite FloodIMG: Karanjit, R., Pally, R., Samadi, S. (2023). *Data in Brief*, doi:10.1016/j.dib.2023.109164.
