"""Fixtures for the Playwright regression suite.

Two Django servers are started from the same backend: one rendering the
refactored frontend (``new``) and one rendering the original pre-refactor
frontend via ``rf_calculator.settings_legacy`` (``legacy``). Tests run the same
browser flow against both to prove the JavaScript changes introduced no
regression.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import cv2
import numpy as np
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until_up(url: str, timeout: float = 40.0) -> None:
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - polling until the server is ready
            last_err = exc
            time.sleep(0.4)
    raise RuntimeError(f"Server at {url} did not start in time: {last_err}")


def _start_server(settings_module: str) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    env = {
        **os.environ,
        "DEBUG": "True",
        "DJANGO_SETTINGS_MODULE": settings_module,
        "ALLOWED_HOSTS": "127.0.0.1,localhost",
    }
    proc = subprocess.Popen(
        ["uv", "run", "python", "manage.py", "runserver", "--noreload", f"127.0.0.1:{port}"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    _wait_until_up(base_url + "/")
    return proc, base_url


@pytest.fixture(scope="session")
def servers() -> Iterator[dict[str, str]]:
    """Start the ``new`` and ``legacy`` servers; yield their base URLs."""
    procs: list[subprocess.Popen] = []
    urls: dict[str, str] = {}
    try:
        for key, module in (
            ("new", "rf_calculator.settings"),
            ("legacy", "rf_calculator.settings_legacy"),
        ):
            proc, url = _start_server(module)
            procs.append(proc)
            urls[key] = url
        yield urls
    finally:
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def test_image_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A 300x300 PNG. Height < 500 keeps the canvas scaleFactor at 1."""
    path = tmp_path_factory.mktemp("img") / "tlc_plate.png"
    image = np.full((300, 300, 3), 240, dtype=np.uint8)
    cv2.imwrite(str(path), image)
    return str(path)


@pytest.fixture(scope="session")
def blob_image_path(tmp_path_factory: pytest.TempPathFactory) -> str:
    """A 400x300 light image with three dark filled circles for auto-detection."""
    path = tmp_path_factory.mktemp("img") / "blobs.png"
    image = np.full((300, 400, 3), 245, dtype=np.uint8)
    for cx, cy in [(100, 100), (200, 150), (300, 220)]:
        cv2.circle(image, (cx, cy), 22, (40, 40, 40), -1)
    cv2.imwrite(str(path), image)
    return str(path)


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1600, "height": 1200})
    pg = context.new_page()
    yield pg
    context.close()
