from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest


@pytest.fixture(scope="module")
def demo_bank() -> Iterator[str]:
    repository = Path(__file__).resolve().parents[3]
    app = repository / "apps" / "demo-bank"
    base_url = "http://127.0.0.1:3001"
    # Reuse an already-running local demo without starting or stopping its process.
    try:
        with urlopen(f"{base_url}/harbor/servicing", timeout=1) as response:
            ready = response.status == 200
    except URLError:
        ready = False
    if ready:
        yield base_url
        return
    process = subprocess.Popen(
        [
            str(app / "node_modules" / ".bin" / "next"),
            "start",
            "--hostname",
            "127.0.0.1",
            "--port",
            "3001",
        ],
        cwd=app,
        env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    base_url = "http://127.0.0.1:3001"
    try:
        for _ in range(50):
            if process.poll() is not None:
                raise RuntimeError("demo bank exited before readiness")
            try:
                with urlopen(f"{base_url}/harbor", timeout=1) as response:
                    if response.status == 200:
                        break
            except URLError:
                time.sleep(0.1)
        else:
            raise RuntimeError("demo bank did not become ready")
        yield base_url
    finally:
        process.terminate()
        process.wait(timeout=10)
