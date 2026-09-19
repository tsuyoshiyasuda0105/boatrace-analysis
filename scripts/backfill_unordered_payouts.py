"""Fill the 3連複 / 2連複 payout columns on existing as-of feature rows.

  python scripts/backfill_unordered_payouts.py --db <feature db> --dry-run
  python scripts/backfill_unordered_payouts.py --db <feature db>

Schema v12 added result/payout columns for sanrenpuku (3連複) and nirenpuku
(2連複).  Rows built before v12 hold NULL there.  Rebuilding every feature row
just to gain them would take hours and would rewrite values that are already
correct, so this fills only the new columns in place and moves the row to v12.
No other column is touched.

The values come from the same function the builder uses
(``asof_builder._winning_payouts`` with ``unordered=True``): winners are
derived from the finishing order and matched against race_payouts, reading
both "1=2=3" and "1-2-3" and collapsing the same payout recorded twice.
Results are facts known after the race, exactly like the existing
sanrentan / nirentan / tansho payout columns, so no as-of cutoff applies.

The source database is opened read-only.  To ship the new values to
production afterwards, emit a column patch in exactly ``PATCH_COLUMNS`` order
(the production side appends columns it does not have yet in the order the
patch lists them, and nightly deltas are later matched by column order):

  python scripts/emit_column_patch.py --source <feature db> \\
      --columns <PATCH_COLUMNS joined by ","> --out data/patch_unordered_<n>.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.features.asof_builder import (  # noqa: E402
    BET_PAYOUT_SOURCES,
    UNORDERED_RESULT_COLUMNS,
    _winning_payouts,
    create_output_schema,
)

DEFAULT_DB = ROOT / "data" / "kachisuji_search.db"
DEFAULT_SOURCE = ROOT / "data" / "boatrace.db"
SCHEMA = 12
BATCH = 20000
UNORDERED_SOURCES = tuple(item for item in BET_PAYOUT_SOURCES if item[3])
# 本番へ送る継ぎ当ての列の順番。新しい列は本番でこの順に末尾へ足されるので、
# 特徴量の定義 (UNORDERED_RESULT_COLUMNS) と同じ順でなければならない。
PATCH_COLUMNS = tuple(name for name, _kind in UNORDERED_RESULT_COLUMNS) + ("schema_version",)


def compute(results: list[dict], payouts: list[dict]) -> tuple[tuple, list[str]]:
    """1 レースぶんの 8 列の値 (UNORDERED_RESULT_COLUMNS の順) と警告を返す。"""
    values: list = []
    warnings: list[str] = []
    has_results = any(item.get("finishing_position") is not None for item in results)
    for kind, legs, source_kind, _unordered in UNORDERED_SOURCES:
        winners = amounts = None
        if has_results:
            winners, amounts, error = _winning_payouts(
                results, payouts, source_kind, legs, unordered=True
            )
            if error is not None:
                warnings.append(f"{kind}: {error}")
        representative = winners[0] if winners and amounts else None
        values += [
            representative,
            amounts[representative] if representative and amounts else None,
            json.dumps(winners, separators=(",", ":")) if winners and amounts else None,
            json.dumps(amounts, separators=(",", ":"), sort_keys=True) if winners and amounts else None,
        ]
    return tuple(values), warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB, help="書き込む特徴量DB")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="読むだけの本体DB")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    if not a.db.is_file():
        print(f"特徴量DBがありません: {a.db}", file=sys.stderr)
        return 2
    if not a.source.is_file():
        print(f"本体DBがありません: {a.source}", file=sys.stderr)
        return 2
    if a.db.resolve() == a.source.resolve() or a.db.name == "boatrace.db":
        print("書き込み先に本体DB (boatrace.db) は指定できません", file=sys.stderr)
        return 2

    if not a.dry_run:
        # 列が無ければ末尾に足す (既存の値には触れない)。
        schema = sqlite3.connect(a.db, timeout=120)
        create_output_schema(schema)
        schema.close()

    # 読み切ってから書く (backfill_entry_change_rate と同じ理由: 読み手の共有
    # ロックと書き手の排他ロックの取り合いで進まなくなる)。
    print("対象行を読み込み中…", flush=True)
    read = sqlite3.connect(f"file:{a.db.resolve().as_posix()}?mode=ro", uri=True, timeout=60)
    targets = [
        row[0]
        for row in read.execute(
            "SELECT race_id FROM asof_race_features WHERE schema_version < ?", (SCHEMA,)
        )
    ]
    read.close()
    print(f"  対象 {len(targets):,} 行", flush=True)
    if not targets:
        print(f"  すでに全行が v{SCHEMA} です。")
        return 0

    print("着順と払戻を読み込み中…", flush=True)
    wanted = set(targets)
    source = sqlite3.connect(f"file:{a.source.resolve().as_posix()}?mode=ro", uri=True, timeout=60)
    source.row_factory = sqlite3.Row
    results: dict[str, list[dict]] = defaultdict(list)
    for row in source.execute("SELECT race_id, boat_number, finishing_position FROM race_results"):
        if row["race_id"] in wanted:
            results[row["race_id"]].append(dict(row))
    payouts: dict[str, list[dict]] = defaultdict(list)
    kinds = tuple(item[2] for item in UNORDERED_SOURCES)
    assert kinds == ("trio", "quinella"), kinds
    for row in source.execute(
        "SELECT race_id, bet_type, combination, payout FROM race_payouts "
        "WHERE bet_type IN (?, ?)",
        kinds,
    ):
        if row["race_id"] in wanted:
            payouts[row["race_id"]].append(dict(row))
    source.close()

    print("計算中…", flush=True)
    updates: list[tuple] = []
    filled = Counter()
    warned = Counter()
    for race_id in targets:
        values, warnings = compute(results.get(race_id, []), payouts.get(race_id, []))
        for (kind, *_), offset in zip(UNORDERED_SOURCES, (0, 4)):
            if values[offset + 2] is not None:
                filled[kind] += 1
        for message in warnings:
            warned[message.split(":", 1)[0]] += 1
        updates.append((*values, SCHEMA, race_id))
    results.clear()
    payouts.clear()
    for kind, *_ in UNORDERED_SOURCES:
        print(f"  {kind}: 値が入った行 {filled[kind]:,} / 判定不能 {warned[kind]:,}", flush=True)

    if a.dry_run:
        print("  --dry-run のため書き込みませんでした。")
        return 0
    if a.limit:
        updates = updates[: a.limit]
        print(f"  --limit のため {len(updates):,} 行だけ書きます。", flush=True)

    assignments = ", ".join(f"{name}=?" for name in PATCH_COLUMNS)
    sql = f"UPDATE asof_race_features SET {assignments} WHERE race_id=?"
    write = sqlite3.connect(a.db, timeout=120)
    write.execute("PRAGMA synchronous=NORMAL")
    started = time.time()
    for offset in range(0, len(updates), BATCH):
        chunk = updates[offset : offset + BATCH]
        write.executemany(sql, chunk)
        write.commit()
        done = offset + len(chunk)
        rate = done / max(time.time() - started, 0.001)
        left = (len(updates) - done) / rate if rate else 0
        print(f"  {done:,} / {len(updates):,} 行  "
              f"({rate:,.0f} 行/秒, 残り {left / 60:.1f} 分)", flush=True)
    write.close()
    print()
    print(f"  {len(updates):,} 行を書きました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
