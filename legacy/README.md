# Legacy: v1 supervised baselines

Aggregate metrics from the original GroundTruth final-year project (2025–26), kept because the write-up quotes
them as the supervised baselines that the zero-shot pipeline replaces:

- `v1-results/external-alleyfloodnet/<backbone>/` — seven backbones evaluated on AlleyFloodNet (out of
  distribution), three seeds each. DINOv3: 98.84% on the CVFD test split, **74.1%** on AlleyFloodNet.
- `v1-results/ablations/` — no-pretrain, no-augmentation, no-warmup and single-stage ablations per backbone.
- `v1-results/flood_classifier_results.pdf` — preliminary classifier report.

Training code, checkpoints and Grad-CAM images were not carried over; the original repository holds them.
