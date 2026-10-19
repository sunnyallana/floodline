"""Run manifests: every stage writes one, so every number in a report traces back to code, model and params."""

from __future__ import annotations

import json
import platform
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from floodline import __version__


def git_sha(cwd: Path | None = None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


@dataclass
class RunManifest:
    stage: str
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    finished_at: str | None = None
    seconds: float | None = None
    git_sha: str | None = field(default_factory=git_sha)
    package_version: str = __version__
    host: dict[str, str] = field(
        default_factory=lambda: {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        }
    )

    def __post_init__(self) -> None:
        self._t0 = time.monotonic()

    @property
    def elapsed(self) -> float:
        """Seconds since the manifest was created."""
        return time.monotonic() - self._t0

    def finish(self, **metrics: Any) -> RunManifest:
        self.metrics.update(metrics)
        self.finished_at = datetime.now(UTC).isoformat(timespec="seconds")
        self.seconds = round(self.elapsed, 1)
        return self

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        return path
