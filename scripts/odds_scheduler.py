"""
オッズスナップショット スケジューラー

毎分起動 → race_closed_at を見て、各レースに対して:
  - 締切 24h 前 ±2分 (大きいレースのみ): T-1d スナップショット
  - 締切 5分前 ±1分: T-5min
  - 締切 1分前 ±30秒: T-1min
  - 締切後 30分以内: final (race_results が入ったら)

「大きいレース」判定:
  - race_grade_number IN (1, 2)  (SG / G1)
  - is_yusho = 1
  - is_jun_yusho = 1

Windows Task Scheduler から毎分起動する想定:
    schtasks /Create /TN "BoatraceOddsScheduler" /SC MINUTE /MO 1 ...

usage:
    python scripts/odds_scheduler.py        # 1回スキャンして終了 (cron用)
    python scripts/odds_scheduler.py --daemon  # 常駐モード (60秒ループ)
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors.odds import collect_one_race
from src.db.connection import connect as db_connect


# JST (UTC+9) ベースで race_closed_at が記録されている前提
JST = timezone(timedelta(hours=9))


# 各 snapshot label のターゲット時間と許容ウィンドウ
# (label, minutes_before_close, tolerance_minutes)
#
# 設計方針 (2026-05-15 〜):
#   全レースのスクレイピングは boatrace.jp BAN リスクがあるので、
#   L4 候補レースのみを締切 5 分前〜1 分前まで 1 分おきに取得する。
#   1 日 ~168 レース × 6 回 = ~1000 req/日 → ~18 候補 × 5 回 = ~90 req/日 に削減。
#
# T-15min は廃止 (Pro 期待値モニタ用だったが、コスト >> ベネフィット)。
# 必要なら個別にスポット取得スクリプトを別途用意する設計。
SNAPSHOT_RULES = [
    ("T-5min", 5, 0.5),    # 締切5分前 ±30秒
    ("T-4min", 4, 0.5),    # 締切4分前 ±30秒
    ("T-3min", 3, 0.5),    # 締切3分前 ±30秒
    ("T-2min", 2, 0.5),    # 締切2分前 ±30秒
    ("T-1min", 1, 0.5),    # 締切1分前 ±30秒
]
# 大きいレース (SG/G1/優勝戦/準優勝) は前日 T-1d も取得 (L4 判定の事前材料)
BIG_SNAPSHOT_RULES = [
    ("T-1d", 24 * 60, 5),  # 24時間前 ±5分
] + SNAPSHOT_RULES

# L4 候補判定の閾値 (web/app.py の _evaluate_morning_l4 と同じ)
# 1号艇 A1: prob_first 0.65-0.85
# 1号艇 A2: prob_first 0.55-0.75
# B 除外会場では候補にしない
EXCLUDE_B_VENUES = {2, 4, 7, 8, 10, 19, 21, 24}
L4_A1_PROB_MIN, L4_A1_PROB_MAX = 0.65, 0.85
L4_A2_PROB_MIN, L4_A2_PROB_MAX = 0.55, 0.75


def _get_l4_candidate_race_ids(target_dates: list[str]) -> set[str]:
    """指定日範囲の L4 候補 race_id を返す。
    判定: predictions テーブル (boat_number=1 の prob_first) + race_entries (1号艇クラス)。
    predictions が未生成の日は何も返さない。
    呼び出し側で「空集合 → 取得対象なし」と扱う。
    """
    if not target_dates:
        return set()
    placeholders = ",".join("?" for _ in target_dates)
    excluded = sorted(EXCLUDE_B_VENUES)
    excl_ph = ",".join("?" for _ in excluded)
    sql = f"""
        SELECT r.race_id
          FROM races r
          JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
          JOIN predictions p ON p.race_id = r.race_id AND p.boat_number = 1
         WHERE r.race_date IN ({placeholders})
           AND r.stadium_number NOT IN ({excl_ph})
           AND (
                (e.class_number = 1 AND p.prob_first BETWEEN ? AND ?)
             OR (e.class_number = 2 AND p.prob_first BETWEEN ? AND ?)
           )
    """
    params = list(target_dates) + [int(v) for v in excluded] + [
        L4_A1_PROB_MIN, L4_A1_PROB_MAX,
        L4_A2_PROB_MIN, L4_A2_PROB_MAX,
    ]
    with db_connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return {r[0] for r in rows}


def _is_big_race(race: dict) -> bool:
    g = race.get("race_grade_number")
    return (g in (1, 2)) or bool(race.get("is_yusho")) or bool(race.get("is_jun_yusho"))


def _parse_close_jst(closed_at, race_date) -> datetime:
    """
    race_closed_at は 'HH:MM:SS' or 'YYYY-MM-DD HH:MM:SS' or
    datetime オブジェクト (psycopg3 from Supabase) のいずれかを許容。
    JST のローカルタイム値として扱い、TZ 付きで返す。
    """
    # psycopg3 が Postgres TIMESTAMP を datetime オブジェクトで返すケースに対応
    if isinstance(closed_at, datetime):
        t = closed_at
        if t.tzinfo is None:
            t = t.replace(tzinfo=JST)
        return t
    # 文字列ケース
    if not isinstance(closed_at, str):
        return None
    s = closed_at.strip()
    try:
        if " " in s and len(s) >= 16:
            # 'YYYY-MM-DD HH:MM[:SS]'
            t = datetime.fromisoformat(s)
        else:
            # 'HH:MM:SS' のみ → race_date と合成
            time_part = s if len(s) >= 5 else f"{s}:00"
            # race_date も datetime/date 対応
            rd_str = race_date.isoformat() if hasattr(race_date, "isoformat") else str(race_date)
            t = datetime.fromisoformat(f"{rd_str} {time_part}")
    except (ValueError, TypeError):
        return None
    return t.replace(tzinfo=JST)


def find_due_snapshots(now_jst: datetime, lookahead_min: int = 30) -> list[tuple[str, str]]:
    """
    今この瞬間に取得すべき (race_id, snapshot_label) のリストを返す。
    既に同 label が取得済みなら除外。

    L4 候補 (predictions ベース) + 大きいレース (SG/G1/優勝戦) のみ対象。
    boatrace.jp BAN リスク軽減のためスクレイピング対象を厳選する。

    safety net: 当日の L4 候補が 1 件も無い場合 (cache_predictions 失敗等)
    は全レース対象にフォールバック (= 元の挙動)。
    """
    sql = """
        SELECT r.race_id, r.race_date, r.race_closed_at,
               r.race_grade_number, r.is_yusho, r.is_jun_yusho
          FROM races r
         WHERE r.race_date BETWEEN ? AND ?
           AND r.race_closed_at IS NOT NULL
    """
    target_dates = [
        (now_jst - timedelta(days=2)).date().isoformat(),
        (now_jst - timedelta(days=1)).date().isoformat(),
        now_jst.date().isoformat(),
        (now_jst + timedelta(days=1)).date().isoformat(),
        (now_jst + timedelta(days=2)).date().isoformat(),
    ]
    with db_connect() as conn:
        rows = conn.execute(
            sql,
            (target_dates[0], target_dates[-1]),
        ).fetchall()
        # 既に取得済みの (race_id, snapshot_label) セット
        existing = set()
        for r in conn.execute(
            "SELECT DISTINCT race_id, snapshot_label FROM odds_trifecta WHERE snapshot_label IS NOT NULL"
        ).fetchall():
            existing.add((r[0], r[1]))

    # L4 候補レース ID 集合 (predictions ベース)
    l4_candidates = _get_l4_candidate_race_ids(target_dates)
    # フォールバック: 当日 (today_iso) の L4 候補が 0 件 → predictions 未生成と
    # みなして全レースを対象 (safety net)
    today_iso = now_jst.date().isoformat()
    today_candidates = {rid for rid in l4_candidates if rid.startswith(today_iso.replace("-", ""))}
    use_l4_filter = len(today_candidates) > 0

    due: list[tuple[str, str]] = []
    keys = ["race_id", "race_date", "race_closed_at",
            "race_grade_number", "is_yusho", "is_jun_yusho"]
    for row in rows:
        race = dict(zip(keys, row))
        rid = race["race_id"]
        is_big = _is_big_race(race)
        # L4 フィルタ: 候補 OR 大きいレース のみ通過 (predictions あり時のみ)
        if use_l4_filter and rid not in l4_candidates and not is_big:
            continue
        close = _parse_close_jst(race["race_closed_at"], race["race_date"])
        if close is None:
            continue
        rules = BIG_SNAPSHOT_RULES if is_big else SNAPSHOT_RULES
        for label, mins_before, tol in rules:
            target = close - timedelta(minutes=mins_before)
            delta = (now_jst - target).total_seconds() / 60.0
            if -tol <= delta <= tol:
                if (rid, label) in existing:
                    continue
                due.append((rid, label))
    return due


def run_one_pass(verbose: bool = False) -> dict:
    """1回スキャン → 該当レースに対しスナップショット取得 + ペーパートレード自動記録"""
    now_jst = datetime.now(tz=JST)
    due = find_due_snapshots(now_jst)
    if verbose:
        print(f"[{now_jst.strftime('%H:%M:%S')}] due snapshots: {len(due)}")

    summary = {"now": now_jst.isoformat(), "n_due": len(due), "n_done": 0,
               "n_paper_signals": 0, "items": []}
    for race_id, label in due:
        try:
            r = collect_one_race(race_id, snapshot_label=label)
            summary["items"].append(r)
            if r.get("odds_inserted", 0) > 0:
                summary["n_done"] += 1
            if verbose:
                print(f"  {race_id} [{label}] inserted={r.get('odds_inserted', 0)}")

            # T-5min スナップショット取得直後にペーパートレード記録
            if label == "T-5min" and r.get("odds_inserted", 0) > 0:
                try:
                    n_sig = _auto_paper_trade(race_id, verbose=verbose)
                    summary["n_paper_signals"] += n_sig
                except Exception as e:
                    if verbose:
                        print(f"    paper_trade record FAILED: {e}")
        except Exception as e:
            if verbose:
                print(f"  {race_id} [{label}] ERROR: {e}")
    return summary


def _auto_paper_trade(race_id: str, verbose: bool = False) -> int:
    """T-5min オッズ確定時にペーパートレード記録"""
    from datetime import datetime as _dt
    with db_connect() as conn:
        # paper_trades テーブル存在確認 (なければ作る)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                race_date TEXT NOT NULL,
                strategy TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                combination TEXT NOT NULL,
                bet_amount INTEGER NOT NULL DEFAULT 100,
                recorded_at TEXT NOT NULL,
                t5_favorite_payout INTEGER,
                t5_judgment TEXT,
                settled INTEGER NOT NULL DEFAULT 0,
                payout INTEGER,
                hit INTEGER,
                roi REAL,
                UNIQUE(race_id, strategy, bet_type, combination)
            );
            CREATE INDEX IF NOT EXISTS idx_paper_trades_date ON paper_trades(race_date);
        """)

        # T-5min での最低オッズ取得
        cur = conn.execute(
            "SELECT MIN(odds) FROM odds_trifecta WHERE race_id = ? AND snapshot_label = 'T-5min'",
            (race_id,),
        )
        row = cur.fetchone()
        t5_min_odds = row[0] if row else None
        if t5_min_odds is None:
            return 0

        t5_payout = int(t5_min_odds * 100)
        if t5_payout >= 2000:  # +EV ゾーン外
            return 0

        # レース情報取得
        cur = conn.execute(
            """SELECT r.race_date, r.race_grade_number, e.class_number
               FROM races r
               JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
               WHERE r.race_id = ?""",
            (race_id,),
        )
        info_row = cur.fetchone()
        if not info_row:
            return 0
        race_date, grade, cls1 = info_row

        now_iso = _dt.now().isoformat()
        n_recorded = 0

        # 戦略パターン
        strategies = []
        if 500 <= t5_payout < 1000:
            strategies.extend([
                ("win_1_500_1k", "win", "1", "本命500-1k → 1号艇単勝 ROI +27.41%"),
                ("tri_123_500_1k", "trifecta", "1-2-3", "本命500-1k → 3連単1-2-3 ROI +44.23%"),
                ("exa_12_500_1k", "exacta", "1-2", "本命500-1k → 2連単1-2 ROI +27.48%"),
            ])
            if grade == 5 and cls1 == 3:
                strategies.append(("ippan_b1_500_1k", "win", "1", "一般戦+B1+本命 → 1号艇 ROI +35.39%"))
            if grade in (1, 2) and cls1 == 1:
                strategies.append(("sgg1_a1_500_1k", "win", "1", "SG/G1+A1+本命 → 1号艇 ROI +27.03%"))
        elif t5_payout < 500:
            strategies.append(("win_1_super_fav", "win", "1", "超本命<500 → 1号艇 ROI +18.45%"))
        elif 1000 <= t5_payout < 2000:
            strategies.append(("win_1_1k_2k", "win", "1", "やや本命1k-2k → 1号艇 ROI +17.92%"))

        for strategy, bet_type, combo, judgment in strategies:
            cur = conn.execute(
                """INSERT OR IGNORE INTO paper_trades
                   (race_id, race_date, strategy, bet_type, combination, recorded_at,
                    t5_favorite_payout, t5_judgment)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (race_id, race_date, strategy, bet_type, combo, now_iso, t5_payout, judgment),
            )
            if cur.rowcount > 0:
                n_recorded += 1
                if verbose:
                    print(f"    paper: {strategy} → {bet_type} {combo}")
        conn.commit()
        return n_recorded


def daemon_loop(interval_sec: int = 60, verbose: bool = False) -> None:
    while True:
        try:
            run_one_pass(verbose=verbose)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[ERROR] {e}")
        time.sleep(interval_sec)


def main():
    import random
    p = argparse.ArgumentParser()
    p.add_argument("--daemon", action="store_true", help="60秒ループで常駐")
    p.add_argument("--interval", type=int, default=60)
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--no-jitter", action="store_true",
                   help="ランダムジッタを無効 (デバッグ用)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # 起動時ランダムジッタ (0-25 秒): Task Scheduler の毎分起動と T-X 判定の
    # tolerance ±0.5min (30秒) の関係で 25秒以内に抑える。これで boatrace.jp
    # 側のアクセスパターンが「毎分 xx:01」固定にならず人間の閲覧に寄る。
    if not args.daemon and not args.no_jitter:
        jitter = random.uniform(0, 25)
        time.sleep(jitter)

    if args.daemon:
        daemon_loop(interval_sec=args.interval, verbose=args.verbose)
    else:
        s = run_one_pass(verbose=args.verbose)
        print(f"due={s['n_due']} done={s['n_done']}")


if __name__ == "__main__":
    main()
