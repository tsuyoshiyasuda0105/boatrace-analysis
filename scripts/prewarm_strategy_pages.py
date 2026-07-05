from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-prewarm")

from src.web.app import create_app  # noqa: E402


def _hit(client, path: str) -> tuple[int, int]:
    resp = client.get(path)
    body = resp.get_data() or b""
    return resp.status_code, len(body)


def main() -> int:
    today = date.today()
    default_from = (today - timedelta(days=30)).isoformat()
    default_to = today.isoformat()
    monthly_from = "2024-06-01"
    monthly_to = today.isoformat()

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["is_member"] = True

    targets = [
        f"/member/strategy?from={default_from}&to={default_to}&recompute=1",
        f"/member/strategy/monthly?recompute=1",
        f"/member/strategy?from={default_from}&to={default_to}",
        "/member/strategy/monthly",
    ]

    ok = True
    for path in targets:
        status, size = _hit(client, path)
        print(f"{path} status={status} bytes={size}", flush=True)
        if status != 200:
            ok = False

    print(
        f"[prewarm] default_range={default_from}..{default_to} "
        f"monthly_range={monthly_from}..{monthly_to}",
        flush=True,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
