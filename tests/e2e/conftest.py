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
BASE_URL = "http://127.0.0.1:8090"


def _port_is_open() -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", 8090)) == 0


@pytest.fixture(scope="session")
def strategy_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("kachisuji_round1") / "strategies.sqlite3"


@pytest.fixture(scope="session")
def kachisuji_server(strategy_db_path: Path):
    assert not _port_is_open(), "port 8090 is already occupied"
    search_db = (PROJECT_ROOT / "data" / "kachisuji_search.db").resolve()
    forbidden_db = (PROJECT_ROOT / "data" / "boatrace.db").resolve()
    assert search_db.is_file()
    assert search_db != forbidden_db
    assert strategy_db_path.resolve().is_relative_to(strategy_db_path.parent.resolve())

    env = os.environ.copy()
    env["KACHISUJI_DB"] = str(search_db)
    env["KACHISUJI_STRATEGY_DB"] = str(strategy_db_path.resolve())
    log_path = strategy_db_path.with_suffix(".server.log")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "scripts/run_kachisuji_web.py", "--port", "8090"],
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
                raise RuntimeError("kachisuji server did not become ready on port 8090")
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
            assert not _port_is_open(), "fixture teardown left port 8090 listening"


@pytest.fixture(scope="session")
def browser(kachisuji_server: str):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture()
def page(browser, kachisuji_server: str):
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    console_errors: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.goto(kachisuji_server, wait_until="networkidle")
    yield page
    page.close()
    unexpected = [item for item in console_errors if "status of 400 (BAD REQUEST)" not in item]
    assert not unexpected, "browser console errors: " + " | ".join(unexpected)
