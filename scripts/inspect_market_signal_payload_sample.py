from __future__ import annotations

import json
import sqlite3
from pathlib import Path


DB = Path("C:/boat_project/boatrace-analysis/data/boatrace.db")


def main() -> None:
    with sqlite3.connect(DB) as conn:
        row = conn.execute(
            "SELECT cache_key, html FROM page_html_cache WHERE cache_key = ?",
            ("market_signals:v3:2026-06-03",),
        ).fetchone()
    print(row[0])
    payload = json.loads(row[1])
    print(payload.keys())
    print({k: type(v).__name__ for k, v in payload.items() if k != "signals"})
    signals = payload.get("signals") or {}
    print("signals", len(signals))
    for i, (rid, sig) in enumerate(signals.items()):
        if i >= 3:
            break
        print("RID", rid)
        text = json.dumps(sig, ensure_ascii=True, indent=2)[:2000]
        print(text)


if __name__ == "__main__":
    main()
