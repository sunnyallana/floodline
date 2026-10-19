"""Logging setup shared by the CLI and the stages."""

from __future__ import annotations

import logging
import sys

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=FORMAT,
        datefmt="%H:%M:%S",
        stream=sys.stderr,
        force=True,
    )
    # Third-party chatter that drowns our own progress lines.
    for noisy in ("urllib3", "huggingface_hub", "transformers", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
