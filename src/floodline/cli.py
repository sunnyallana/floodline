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

    d = sub.add_parser("depth", help="metric depth maps with Depth Anything V2 (CPU)")
    d.add_argument("--model", default=ModelIds.depth)
    d.add_argument("--limit", type=int)
    d.add_argument("--threads", type=int)

    g = sub.add_parser("segment", help="SAM 3 concept segmentation scored against the CVFD masks (CPU)")
    g.add_argument("--prompts", nargs="+", default=list(SAM3_PROMPTS))
    g.add_argument("--limit", type=int)
    g.add_argument("--threshold", type=float, default=0.5)
    g.add_argument("--threads", type=int)

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

            sheets.run(paths, n=args.n, per_sheet=args.per_sheet, seed=args.seed)
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
