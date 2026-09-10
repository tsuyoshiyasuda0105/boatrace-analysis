# -*- coding: utf-8 -*-
"""既存行の一部の列だけを本番へ届ける「継ぎ当て」ファイルを作る。

  python scripts/emit_column_patch.py --columns b1_entry_change_rate,... --out data/patch_x.db

後から特徴量に列を足したとき、その値を過去の全行へ届けたい。行を丸ごと運ぶ
デルタだと 1 行 1KB 強あり 638MB になるが、race_id と対象の列だけなら 56MB
で済む (2026-09-10: 進入変更率 569,082 行)。

出力は SQLite ファイル 1 つ。表は ``asof_column_patch``、先頭列が race_id で、
続けて書き換えたい列が並ぶ。適用側 (src/kachisuji/delta_transport) は名前が
``patch_`` で始まるファイルだけをこの形として扱い、行は増やさない。
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.kachisuji.delta_transport import PATCH_TABLE  # noqa: E402

DEFAULT_SOURCE = ROOT / "data" / "kachisuji_search.db"
# 適用側と同じ狭さ。ここで弾いておけば、本番まで運ばれない。
COLUMN_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")
CHUNK = 50_000


def _columns(raw: str) -> list[str]:
    names = [part.strip() for part in raw.split(",") if part.strip()]
    if not names:
        raise SystemExit("--columns が空です")
    bad = [name for name in names if not COLUMN_NAME.fullmatch(name)]
    if bad:
        raise SystemExit(f"列名として受け付けられません: {bad}")
    if "race_id" in names:
        raise SystemExit("race_id は自動で入ります。--columns には書かないでください")
    if len(set(names)) != len(names):
        raise SystemExit("同じ列が2回指定されています")
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--columns", required=True, help="カンマ区切りの列名")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--date-from")
    ap.add_argument("--date-to")
    a = ap.parse_args()

    if not a.out.name.startswith("patch_"):
        raise SystemExit(f"出力名は patch_ で始めてください: {a.out.name}")
    if a.out.exists():
        raise SystemExit(f"すでにあります: {a.out}")
    columns = _columns(a.columns)

    source = sqlite3.connect(f"file:{a.source}?mode=ro", uri=True, timeout=60)
    # 型は元の表から写す。すべて REAL にすると、着順 "1-2-3" や潮 "満潮前後"
    # のような文字列の列を運んだときに型が崩れる (2026-09-10 に気づいた)。
    have = {
        str(row[1]): (str(row[2]) or "")
        for row in source.execute("PRAGMA table_info(asof_race_features)")
    }
    missing = [name for name in columns if name not in have]
    if missing:
        raise SystemExit(f"元の DB にその列がありません: {missing}")

    where = ""
    params: list[str] = []
    if a.date_from:
        where += " WHERE race_date >= ?"
        params.append(a.date_from)
    if a.date_to:
        where += (" AND" if where else " WHERE") + " race_date <= ?"
        params.append(a.date_to)

    out = sqlite3.connect(a.out)
    out.execute(
        f"CREATE TABLE {PATCH_TABLE} (race_id TEXT PRIMARY KEY, "
        + ", ".join(f"{name} {have[name]}".rstrip() for name in columns)
        + ")"
    )
    placeholders = ",".join("?" for _ in range(len(columns) + 1))
    rows = source.execute(
        f"SELECT race_id, {', '.join(columns)} FROM asof_race_features{where}",
        params,
    )
    written = 0
    while True:
        chunk = rows.fetchmany(CHUNK)
        if not chunk:
            break
        out.executemany(f"INSERT INTO {PATCH_TABLE} VALUES ({placeholders})", chunk)
        written += len(chunk)
        print(f"  {written:,} 行", flush=True)
    out.commit()
    source.close()
    out.execute("VACUUM")
    out.close()

    size = a.out.stat().st_size
    print()
    print(f"  {a.out} を作りました")
    print(f"  {written:,} 行 / {len(columns)} 列 / {size / 1e6:,.1f} MB")
    print(f"  対象の列: {', '.join(columns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
