"""Append-only result stores that let a long CPU run be killed and restarted without losing work."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any


class JsonlStore:
    """One JSON object per line, keyed by ``key`` (default ``image_id``). Writes are flushed immediately."""

    def __init__(self, path: Path, key: str = "image_id") -> None:
        self.path = path
        self.key = key
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def done(self) -> set[str]:
        return {rec[self.key] for rec in self}

    def __iter__(self) -> Iterator[dict[str, Any]]:
        if not self.path.exists():
            return iter(())
        with self.path.open(encoding="utf-8") as f:
            return iter([json.loads(line) for line in f if line.strip()])

    def append(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()


class CsvStore:
    """Row-oriented store with a stable header. Rows may carry different keys; the union becomes the header."""

    def __init__(self, path: Path, key: str = "image_id") -> None:
        self.path = path
        self.key = key
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open(newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def done(self) -> set[str]:
        return {r[self.key] for r in self.rows()}

    def extend(self, new_rows: Iterable[dict[str, Any]]) -> None:
        new_rows = list(new_rows)
        if not new_rows:
            return
        existing = self.rows()
        header: list[str] = []
        for r in (*existing, *new_rows):
            for k in r:
                if k not in header:
                    header.append(k)
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(existing)
            w.writerows(new_rows)
        tmp.replace(self.path)  # atomic on the same filesystem; a crash never leaves a half-written CSV
