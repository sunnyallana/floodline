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
    p.add_argument(
        "--dataset",
        default="cvfd",
        choices=["cvfd", "euflood"],
        help="image set for depth/segment/waterline/assess; non-default sets write to results/<dataset>/",
    )
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

    f = sub.add_parser("fuse", help="join per-hex layers and located reports, and rank hexes")
    f.add_argument("layers", nargs="+", help="CSVs keyed by h3; the first (satellite) defines the hexes")
    f.add_argument("--reports", help="CSV with lat, lon and, when assessed, depth_class, people_*, confidence")
    f.add_argument("--out", default="hexes", help="output stem under results/")

    sat = sub.add_parser("satellite", help="Copernicus EMS flood extent -> satellite_water_fraction per H3 hex")
    sat.add_argument("ems_source", help="directory holding one activation's EMS vector packages")
    sat.add_argument("--out", default="satellite_layer.csv")
    sat.add_argument("--resolution", type=int, default=8, help="H3 resolution: 8 for a province, 9 for a city")

    rc = sub.add_parser("reach", help="OSM travel time to each hex, before and with flooded roads removed")
    rc.add_argument("cells_csv", help="CSV with an h3 column (e.g. results/satellite_layer.csv)")
    rc.add_argument("--ems", required=True, help="EMS directory whose flood extent closes roads")
    rc.add_argument("--staging", nargs="*", default=[], help="lat,lon pairs; default: OSM fire stations, hospitals")
    rc.add_argument("--out", default="reach", help="output stem under results/")

    ex = sub.add_parser("exposure", help="OSM buildings in the flood and in cut-off hexes")
    ex.add_argument("layer_csv", help="satellite layer CSV")
    ex.add_argument("--ems", required=True)
    ex.add_argument("--reach", help="reach CSV, to count buildings in cut-off hexes")
    ex.add_argument("--out", default="exposure.csv")

    sv = sub.add_parser("serve", help="add one city to the static web map (docs/ by default)")
    sv.add_argument("hexes_csv", help="ranked hexes from `floodline fuse`")
    sv.add_argument("--key", required=True, help="short id used in the URL, e.g. prague")
    sv.add_argument("--title", required=True)
    sv.add_argument("--subtitle", default="")
    sv.add_argument("--ems", required=True, help="EMS directory for the flood extent and mapped area")
    sv.add_argument("--roads", help="closed roads GeoJSON from `floodline reach`")
    sv.add_argument("--staging", help="staging points GeoJSON from `floodline reach`")
    sv.add_argument("--photos", help="photo CSV with lat, lon, title, license, source_url")
    sv.add_argument("--out-dir", help="site directory; default docs/")

    sub.add_parser("external", help="index the European Flood 2013 geotagged set under data/external/")

    ag = sub.add_parser("agree", help="share of located photos that fall where the satellite maps water")
    ag.add_argument("photos_csv", help="CSV with an h3 column (e.g. results/eu_flood_2013_mappable.csv)")
    ag.add_argument("layers", nargs="+", help="one or more satellite layer CSVs")
    ag.add_argument("--k", type=int, default=1, help="neighbour rings counted as agreement")
    ag.add_argument("--out", default="agreement")
    ag.add_argument("--ems-dirs", nargs="*", default=[], help="activation directories for metre-distance evidence")

    rl = sub.add_parser("release", help="package the depth benchmark as a Hugging Face dataset")
    rl.add_argument("--repo", default="sunnyallana/flood-depth-benchmark")
    rl.add_argument("--push", action="store_true", help="upload; needs HF_WRITE_TOKEN with write access")

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
    paths = Paths(namespace="" if args.dataset == "cvfd" else args.dataset).ensure()
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

            depth_maps.run(paths, model=args.model, limit=args.limit, threads=args.threads, dataset=args.dataset)
        elif args.command == "segment":
            from floodline.stages import segment

            scores = segment.run(
                paths,
                dataset=args.dataset,
                prompts=args.prompts,
                limit=args.limit,
                threshold=args.threshold,
                threads=args.threads,
            )
            for k, v in segment.summarise_scores(scores).items():
                log.info("%-22s mean %.3f  median %.3f  n=%d", k, v["mean"], v["median"], v["n"])
        elif args.command == "waterline":
            from floodline.stages import waterline

            waterline.run(paths, limit=args.limit, dataset=args.dataset)
        elif args.command == "fuse":
            from pathlib import Path

            from floodline.stages import fuse

            fuse.run(
                paths,
                [Path(p) for p in args.layers],
                Path(args.reports) if args.reports else None,
                out_stem=args.out,
            )
        elif args.command == "satellite":
            from pathlib import Path

            from floodline.stages import satellite

            satellite.run(paths, Path(args.ems_source), out_name=args.out, resolution=args.resolution)
        elif args.command == "reach":
            from pathlib import Path

            import pandas as pd

            from floodline.stages import reach

            cells = pd.read_csv(Path(args.cells_csv)).h3.dropna().astype(str).tolist()
            staging = [tuple(float(x) for x in s.split(",")) for s in args.staging]
            reach.run(paths, cells, Path(args.ems), staging or None, out_stem=args.out)
        elif args.command == "exposure":
            from pathlib import Path

            from floodline.stages import exposure

            exposure.run(
                paths,
                Path(args.layer_csv),
                Path(args.ems),
                Path(args.reach) if args.reach else None,
                out_name=args.out,
            )
        elif args.command == "serve":
            from pathlib import Path

            from floodline.stages import serve

            def opt(v: str | None) -> Path | None:
                return Path(v) if v else None

            serve.run(
                paths,
                Path(args.hexes_csv),
                key=args.key,
                title=args.title,
                ems_source=Path(args.ems),
                subtitle=args.subtitle,
                out_dir=opt(args.out_dir),
                roads=opt(args.roads),
                staging=opt(args.staging),
                photos=opt(args.photos),
            )
        elif args.command == "external":
            from floodline.stages import external

            external.run(paths)
        elif args.command == "agree":
            from pathlib import Path

            from floodline.stages import agreement

            agreement.run(
                paths,
                Path(args.photos_csv),
                [Path(p) for p in args.layers],
                out_stem=args.out,
                k=args.k,
                ems_dirs=[Path(d) for d in args.ems_dirs],
            )
        elif args.command == "release":
            from floodline.stages import release

            release.run(paths, repo=args.repo, push=args.push)
        elif args.command == "overlay":
            from pathlib import Path

            from floodline.stages import segment

            log.info("wrote %s", segment.overlay(paths, args.image_ids, Path(args.out) if args.out else None))
        elif args.command == "assess":
            from pathlib import Path

            from floodline.stages import assess

            assess.run(
                paths,
                dataset=args.dataset,
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
