"""A llama.cpp server as a context manager, plus the one request shape we send it."""

from __future__ import annotations

import base64
import json
import socket
import subprocess
import time
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from floodline.log import get_logger

log = get_logger(__name__)


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


class LlamaServer:
    """Start ``llama-server`` for a GGUF model with a vision projector; reuse one already listening on the port."""

    def __init__(
        self,
        binary: Path,
        model: Path,
        mmproj: Path,
        port: int = 8089,
        threads: int = 20,
        ctx: int = 4096,
        log_path: Path | None = None,
        startup_timeout_s: int = 900,
    ) -> None:
        for p in (binary, model, mmproj):
            if not p.exists():
                raise FileNotFoundError(p)
        self.binary, self.model, self.mmproj = binary, model, mmproj
        self.port, self.threads, self.ctx = port, threads, ctx
        self.log_path = log_path
        self.startup_timeout_s = startup_timeout_s
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> LlamaServer:
        if _port_open(self.port):
            log.info("reusing llama-server already listening on %d", self.port)
            return self
        log_f = self.log_path.open("ab") if self.log_path else subprocess.DEVNULL
        self._proc = subprocess.Popen(
            [
                str(self.binary),
                "-m",
                str(self.model),
                "--mmproj",
                str(self.mmproj),
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "-c",
                str(self.ctx),
                "-t",
                str(self.threads),
                "--temp",
                "0",
                "-np",
                "1",
            ],
            stdout=log_f,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + self.startup_timeout_s
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError(
                    f"llama-server exited during startup (code {self._proc.returncode}); see {self.log_path}"
                )
            if _port_open(self.port):
                try:
                    if requests.get(f"{self.base_url}/health", timeout=2).status_code == 200:
                        log.info("llama-server ready: %s (%d threads)", self.model.name, self.threads)
                        return self
                except requests.RequestException:
                    pass
            time.sleep(1)
        self.__exit__(None, None, None)
        raise TimeoutError(f"llama-server not ready after {self.startup_timeout_s}s")

    def __exit__(self, *exc: object) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None

    def chat_json(
        self,
        image: Image.Image,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 450,
        timeout_s: int = 900,
    ) -> tuple[str, float]:
        """One image + prompt, grammar-constrained to ``schema``. Returns (raw text, seconds)."""
        buf = BytesIO()
        image.save(buf, format="JPEG", quality=92)
        data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.get("title", "out"), "schema": schema},
            },
        }
        t0 = time.monotonic()
        r = requests.post(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
            timeout=timeout_s,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], time.monotonic() - t0

    def text_json(
        self, prompt: str, schema: dict[str, Any], max_tokens: int = 120, timeout_s: int = 300
    ) -> tuple[str, float]:
        """Text-only, grammar-constrained call. Used for the second pass, which reads the first pass's description."""
        body = {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema.get("title", "out"), "schema": schema},
            },
        }
        t0 = time.monotonic()
        r = requests.post(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
            timeout=timeout_s,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"], time.monotonic() - t0


def upscale(image: Image.Image, size: int) -> Image.Image:
    """Bicubic upscale of small sources so the vision encoder sees more than a thumbnail. No-op if already larger."""
    if max(image.size) >= size:
        return image
    return image.resize((size, size), Image.BICUBIC)
