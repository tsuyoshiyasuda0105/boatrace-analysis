# -*- coding: utf-8 -*-
"""オッズ取得の結果を、成功も失敗も1行ずつ残す。

取れれば `odds_trifecta` に行が増え、取れなければ何も起きない。だから
「静かに止まっている」と「今日は対象が少ない」が見分けられなかった。
実際 2026 年に 3 回、止まっているのに誰も気づかなかった:

  - 2026-08-12  T-5min の許容窓が cron 間隔と噛み合わず大半を取り逃した
  - 2026-09-03  odds-cron だけ DATABASE_URL の差し替え漏れで 0 件
  - 2026-07〜09 進入データの収集が止まり、2 か月ぶん欠けた (別経路だが同じ形)

この表は「狙ったのに取れなかった」を残す。件数の鮮度を見るより早く、
理由まで分かる。

書き込みは 1 パスにつき 1 接続だけ。本番の接続枠は 15 で余裕が無いので、
レースごとに繋がない。記録に失敗しても取得は続ける (記録のために本業を
止めない)。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from src.db.connection import connect as db_connect

TABLE = "odds_fetch_status"

# 取れた / 狙ったが空 / 失敗。この 3 つだけで運用の判断がつく。
STATE_OK = "ok"
STATE_EMPTY = "empty"
STATE_ERROR = "error"


def outcome(summary: Mapping[str, Any] | None, error: BaseException | None = None
            ) -> tuple[str, str, int]:
    """1 レース分の結果を (state, detail, 取れた点数) にまとめる。

    ``collect_one_race`` は失敗の理由を ``error`` キーに入れて返すが、
    呼び出し側が捨てていた。例外で落ちた場合は型と本文を残す。
    """
    if error is not None:
        return STATE_ERROR, f"{type(error).__name__}: {error}"[:200], 0
    if not summary:
        return STATE_ERROR, "no summary", 0
    count = int(summary.get("odds_inserted") or 0)
    detail = str(summary.get("error") or "")[:200]
    if count > 0:
        return STATE_OK, detail, count
    return (STATE_ERROR if detail else STATE_EMPTY), detail or "no odds inserted", 0


def ensure_table(conn) -> None:
    """SQLite と Postgres の両方で通る書き方で作る。"""
    if getattr(conn, "_kind", "") == "postgres":
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                race_id TEXT NOT NULL,
                snapshot_label TEXT NOT NULL,
                state TEXT NOT NULL,
                detail TEXT,
                combination_count INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 1,
                checked_at TEXT NOT NULL,
                last_success_at TEXT,
                PRIMARY KEY (race_id, snapshot_label)
            );
            CREATE INDEX IF NOT EXISTS idx_odds_fetch_status_checked
                ON {TABLE}(checked_at, state);
            ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY;
            """
        )
        return
    conn.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            race_id TEXT NOT NULL,
            snapshot_label TEXT NOT NULL,
            state TEXT NOT NULL,
            detail TEXT,
            combination_count INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 1,
            checked_at TEXT NOT NULL,
            last_success_at TEXT,
            PRIMARY KEY (race_id, snapshot_label)
        );
        CREATE INDEX IF NOT EXISTS idx_odds_fetch_status_checked
            ON {TABLE}(checked_at, state);
        """
    )


# 何度も狙った回数を数えつつ、最後に取れた時刻は成功したときだけ進める。
# 「3 回試して 1 回も取れていない」が読み取れるようにするため。
_UPSERT = f"""
INSERT INTO {TABLE} (race_id, snapshot_label, state, detail,
                     combination_count, attempts, checked_at, last_success_at)
VALUES (?, ?, ?, ?, ?, 1, ?, ?)
ON CONFLICT (race_id, snapshot_label) DO UPDATE SET
    state = excluded.state,
    detail = excluded.detail,
    combination_count = excluded.combination_count,
    attempts = {TABLE}.attempts + 1,
    checked_at = excluded.checked_at,
    last_success_at = COALESCE(excluded.last_success_at, {TABLE}.last_success_at)
"""


def record(rows: Sequence[tuple[str, str, str, str, int]], *, db_path=None,
           conn=None) -> int:
    """(race_id, label, state, detail, count) をまとめて残す。

    記録できなくても取得は続けたいので、例外は外に出さず 0 を返す。
    """
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = [
        (race_id, label, state, detail or None, int(count), now,
         now if state == STATE_OK else None)
        for race_id, label, state, detail, count in rows
    ]
    own = conn is None
    try:
        if own:
            conn = db_connect(db_path)
        ensure_table(conn)
        conn.executemany(_UPSERT, payload)
        conn.commit()
        return len(payload)
    except Exception as exc:  # noqa: BLE001 - 記録の失敗で本業を止めない
        print(f"[odds-status] record skipped: {type(exc).__name__}: {exc}", flush=True)
        return 0
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def recent_failures(hours: int = 24, *, db_path=None, conn=None) -> list[dict[str, Any]]:
    """直近で狙ったのに取れていないものを、新しい順に返す。"""
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds"
    )
    own = conn is None
    try:
        if own:
            conn = db_connect(db_path)
        ensure_table(conn)
        rows = conn.execute(
            f"SELECT race_id, snapshot_label, state, detail, attempts, checked_at "
            f"FROM {TABLE} WHERE checked_at >= ? AND state <> ? "
            f"ORDER BY checked_at DESC",
            (since, STATE_OK),
        ).fetchall()
        return [
            {
                "race_id": row[0],
                "snapshot_label": row[1],
                "state": row[2],
                "detail": row[3],
                "attempts": row[4],
                "checked_at": row[5],
            }
            for row in rows
        ]
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def summarize(hours: int = 24, *, db_path=None, conn=None) -> dict[str, int]:
    """直近の state ごとの件数。朝の確認で「0 件でないか」を見る。"""
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds"
    )
    own = conn is None
    try:
        if own:
            conn = db_connect(db_path)
        ensure_table(conn)
        rows = conn.execute(
            f"SELECT state, COUNT(*) FROM {TABLE} WHERE checked_at >= ? GROUP BY state",
            (since,),
        ).fetchall()
        return {str(state): int(count) for state, count in rows}
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def rows_from_pass(items: Iterable[Mapping[str, Any]]) -> list[tuple[str, str, str, str, int]]:
    """run_one_pass の items から記録用の行を作る (成功した分)。"""
    out = []
    for item in items:
        state, detail, count = outcome(item)
        out.append(
            (str(item.get("race_id")), str(item.get("snapshot_label")), state, detail, count)
        )
    return out
