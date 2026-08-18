"""Upload one kachisuji SQLite delta to private Supabase Storage."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote

import requests


BUCKET = "kachisuji-deltas"
DELTA_NAME_RE = re.compile(r"^kachisuji_delta_(\d{8})\.db$")
REQUIRED_TABLES = ("asof_race_features", "racers")


def _storage_headers(service_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {service_key}",
        "apikey": service_key,
    }


def storage_object_name(delta_path: Path) -> str:
    match = DELTA_NAME_RE.fullmatch(delta_path.name)
    if not match:
        raise ValueError(
            "delta filename must be kachisuji_delta_YYYYMMDD.db: "
            f"{delta_path.name}"
        )
    return f"{match.group(1)}.db"


def validate_delta(delta_path: Path) -> None:
    if not delta_path.is_file():
        raise FileNotFoundError(f"delta not found: {delta_path}")
    uri = delta_path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = set(REQUIRED_TABLES) - tables
        if missing:
            raise ValueError(f"delta is missing tables: {', '.join(sorted(missing))}")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("delta failed SQLite quick_check")
    finally:
        connection.close()


def upload_delta(
    delta_path: Path,
    *,
    supabase_url: str,
    service_key: str,
    session: requests.Session | None = None,
    timeout: float = 60.0,
) -> str:
    validate_delta(delta_path)
    object_name = storage_object_name(delta_path)
    client = session or requests.Session()
    url = (
        f"{supabase_url.rstrip('/')}/storage/v1/object/{BUCKET}/"
        f"{quote(object_name, safe='')}"
    )
    headers = _storage_headers(service_key)
    headers.update(
        {
            "Content-Type": "application/octet-stream",
            "x-upsert": "true",
        }
    )
    with delta_path.open("rb") as source:
        response = client.post(url, headers=headers, data=source, timeout=timeout)
    response.raise_for_status()
    return object_name


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delta", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    supabase_url = os.getenv("SUPABASE_URL", "").strip()
    service_key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    if not supabase_url or not service_key:
        print(
            "error: SUPABASE_URL and SUPABASE_SERVICE_KEY are required",
            file=sys.stderr,
        )
        return 2
    try:
        object_name = upload_delta(
            args.delta,
            supabase_url=supabase_url,
            service_key=service_key,
        )
    except Exception as exc:
        print(f"error: kachisuji delta upload failed: {exc}", file=sys.stderr)
        return 1
    print(f"[uploaded] bucket={BUCKET} object={object_name} source={args.delta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
