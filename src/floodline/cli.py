"""Command-line entry point: ``floodline <stage> [options]``.

Stages import their heavy dependencies lazily, so ``floodline --help`` and the light stages work in an
environment without torch or transformers.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from floodline import __version__
from floodline.config import SAM3_PROMPTS, ModelIds, Paths
from floodline.log import get_logger, setup_logging

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="floodline", description=__doc__)
    p.add_argument("--version", action="version", version=f"floodline {__version__}")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("index", help="index the CVFD images and masks into data/cvfd_index.parquet")

    s = sub.add_parser("sheets", help="contact sheets and label template for the depth benchmark")
    s.add_argument("--n", type=int, default=300)
    s.add_argument("--per-sheet", type=int, default=12)
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--split", default="test", help="sample from one split; 'all' for every split")

    d = sub.add_parser("depth", help="metric depth maps with Depth Anything V2 (CPU)")
    d.add_argument("--model", default=ModelIds.depth)
    d.add_argument("--limit", type=int)
    d.add_argument("--threads", type=int)

    g = sub.add_parser("segment", help="SAM 3 concept segmentation scored against the CVFD masks (CPU)")
    g.add_argument("--prompts", nargs="+", default=list(SAM3_PROMPTS))
    g.add_argument("--limit", type=int)
    g.add_argument("--threshold", type=float, default=0.5)
    g.add_argument("--threads", type=int)

    w = sub.add_parser("waterline", help="flood depth from SAM 3 masks + metric depth (no language model)")
    w.add_argument("--limit", type=int)

    f = sub.add_parser("fuse", help="aggregate located reports on H3 hexagons and rank them")
    f.add_argument(
        "reports", help="CSV with lat, lon, depth_class, people_*, confidence, location_confidence, captured_at"
    )
    f.add_argument("--layers", help="optional CSV keyed by h3 with satellite_water_fraction and reach_minutes")
    f.add_argument("--out", default="hexes", help="output stem under results/")

    sat = sub.add_parser("satellite", help="Copernicus EMS flood extent -> satellite_water_fraction per H3 hex")
    sat.add_argument("ems_source", help="EMS vector package directory, or one observedEventA file")
    sat.add_argument("--out", default="satellite_layer.csv")

    rc = sub.add_parser("reach", help="OSM travel time from staging points to each hex in a layer CSV")
    rc.add_argument("cells_csv", help="CSV with an h3 column (e.g. results/satellite_layer.csv)")
    rc.add_argument("--staging", nargs="+", required=True, help="lat,lon pairs, e.g. 24.86,67.00")
    rc.add_argument("--out", default="reach_layer.csv")

    sv = sub.add_parser("serve", help="GeoJSON + MapLibre page for a ranked hex CSV")
    sv.add_argument("hexes_csv")
    sv.add_argument("--title", required=True)
    sv.add_argument("--subtitle", default="")

    o = sub.add_parser("overlay", help="render original | SAM 3 | CVFD mask rows for a few images")
    o.add_argument("image_ids", nargs="+")
    o.add_argument("--out")

    a = sub.add_parser("assess", help="structured assessment per photo with a VLM via llama-server (CPU)")
    a.add_argument("--model", default=ModelIds.vlm_gguf, help="GGUF file name under models/")
    a.add_argument("--mmproj", default=ModelIds.vlm_mmproj, help="vision projector GGUF under models/")
    a.add_argument("--limit", type=int)
    a.add_argument("--threads", type=int)
    a.add_argument("--port", type=int, default=8089)
    a.add_argument("--size", type=int, default=672, help="upscale inputs to this many px before sending")
    a.add_argument("--out", help="JSONL path; defaults to results/vlm_assessments.jsonl")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    paths = Paths().ensure()
    try:
        if args.command == "index":
            from floodline.stages import index

            index.run(paths)
        elif args.command == "sheets":
            from floodline.stages import sheets

            sheets.run(
                paths,
                n=args.n,
                per_sheet=args.per_sheet,
                seed=args.seed,
                split=None if args.split == "all" else args.split,
            )
        elif args.command == "depth":
            from floodline.stages import depth_maps

            depth_maps.run(paths, model=args.model, limit=args.limit, threads=args.threads)
        elif args.command == "segment":
            from floodline.stages import segment

            scores = segment.run(
                paths, prompts=args.prompts, limit=args.limit, threshold=args.threshold, threads=args.threads
            )
            for k, v in segment.summarise_scores(scores).items():
                log.info("%-22s mean %.3f  median %.3f  n=%d", k, v["mean"], v["median"], v["n"])
        elif args.command == "waterline":
            from floodline.stages import waterline

            waterline.run(paths, limit=args.limit)
        elif args.command == "fuse":
            from pathlib import Path

            from floodline.stages import fuse

            fuse.run(paths, Path(args.reports), Path(args.layers) if args.layers else None, out_stem=args.out)
        elif args.command == "satellite":
            from pathlib import Path

            from floodline.stages import satellite

            satellite.run(paths, Path(args.ems_source), out_name=args.out)
        elif args.command == "reach":
            from pathlib import Path

            import pandas as pd

            from floodline.stages import reach

            cells = pd.read_csv(Path(args.cells_csv)).h3.dropna().astype(str).tolist()
            staging = [tuple(float(x) for x in s.split(",")) for s in args.staging]
            reach.run(paths, cells, staging, out_name=args.out)
        elif args.command == "serve":
            from pathlib import Path

            from floodline.stages import serve

            serve.run(paths, Path(args.hexes_csv), title=args.title, subtitle=args.subtitle)
        elif args.command == "overlay":
            from pathlib import Path

            from floodline.stages import segment

            log.info("wrote %s", segment.overlay(paths, args.image_ids, Path(args.out) if args.out else None))
        elif args.command == "assess":
            from pathlib import Path

            from floodline.stages import assess

            assess.run(
                paths,
                model=args.model,
                mmproj=args.mmproj,
                limit=args.limit,
                threads=args.threads,
                port=args.port,
                size=args.size,
                out=Path(args.out) if args.out else None,
            )
    except (FileNotFoundError, RuntimeError, TimeoutError) as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
