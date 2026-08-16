from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from playwright.sync_api import sync_playwright


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PORT = int(os.environ.get("KACHISUJI_E2E_PORT", "8091"))
BASE_URL = f"http://127.0.0.1:{PORT}"


def _port_is_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


@pytest.fixture(scope="session")
def round3_strategy_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("kachisuji_round3") / "strategies.sqlite3"


@pytest.fixture(scope="session")
def round3_server(round3_strategy_db: Path):
    assert not _port_is_open(), f"port {PORT} is already occupied"
    search_db = (PROJECT_ROOT / "data" / "kachisuji_search.db").resolve()
    forbidden_db = (PROJECT_ROOT / "data" / "boatrace.db").resolve()
    assert search_db.is_file() and search_db != forbidden_db

    env = os.environ.copy()
    env["KACHISUJI_DB"] = str(search_db)
    env["KACHISUJI_STRATEGY_DB"] = str(round3_strategy_db.resolve())
    log_path = round3_strategy_db.with_suffix(".server.log")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "scripts/run_kachisuji_web.py", "--port", str(PORT)],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(log_path.read_text(encoding="utf-8"))
                try:
                    with urlopen(f"{BASE_URL}/healthz", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"kachisuji server did not become ready on port {PORT}")
            yield BASE_URL
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            deadline = time.monotonic() + 3
            while _port_is_open() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert process.poll() is not None
            assert not _port_is_open(), f"fixture teardown left port {PORT} listening"


@pytest.fixture(scope="session")
def browser(round3_server: str):
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture()
def page(browser, round3_server: str):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(round3_server, wait_until="networkidle")
    yield page
    page.close()
