"""Stage 0: index the dataset."""

from __future__ import annotations

from floodline.config import Paths
from floodline.cvfd import index_cvfd, summarise
from floodline.log import get_logger
from floodline.runs import RunManifest

log = get_logger(__name__)


def run(paths: Paths) -> int:
    paths.ensure()
    manifest = RunManifest("index", params={"cvfd": str(paths.cvfd)})
    df = index_cvfd(paths.cvfd)
    df.to_parquet(paths.cvfd_index, index=False)
    summary = summarise(df)
    summary.to_csv(paths.results / "cvfd_summary.csv", index=False)
    log.info("indexed %d images -> %s", len(df), paths.cvfd_index)
    log.info("\n%s", summary.to_string(index=False))
    manifest.finish(images=len(df), flooded=int(df.flooded.sum())).write(paths.results / "index_run.json")
    return len(df)
