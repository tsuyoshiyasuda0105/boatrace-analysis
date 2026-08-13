"""Profile TOP-related endpoints with lightweight response headers."""
from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from src.web import app as web_app


@contextmanager
def _member_session(client):
    with client.session_transaction() as session:
        session["is_member"] = True
        session["role"] = "admin"
    yield


def _profile(client, path: str, *, follow_redirects: bool = False) -> dict[str, object]:
    response = client.get(path, follow_redirects=follow_redirects)
    return {
        "path": path,
        "status": response.status_code,
        "elapsed_ms": response.headers.get("X-Boatrace-Elapsed-Ms"),
        "db_query_count": response.headers.get("X-Boatrace-Db-Query-Count"),
        "db_time_ms": response.headers.get("X-Boatrace-Db-Time-Ms"),
        "response_bytes": response.headers.get("X-Boatrace-Response-Bytes"),
        "cache_control": response.headers.get("Cache-Control"),
        "server_timing": response.headers.get("Server-Timing"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="target date YYYY-MM-DD")
    args = parser.parse_args()

    os.environ["BOATRACE_PROFILE_HTTP"] = "1"
    web_app.invalidate_cache()
    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="profile")
    client = app.test_client()

    results: list[dict[str, object]] = []
    results.append(_profile(client, f"/races?date={args.date}", follow_redirects=True))
    with _member_session(client):
        results.append(_profile(client, f"/api/market-signals?date={args.date}"))
        results.append(_profile(client, f"/api/odds-123-timeline?date={args.date}"))
        results.append(_profile(client, f"/member/today-races?date={args.date}"))

    for row in results:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
