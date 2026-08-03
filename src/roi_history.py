from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable, Iterable


STRATEGY_KEY_ALIASES = {
    # Older market-signal snapshots used the scanner class name.  The ROI
    # registry later standardized this as the venue/formulation key below.
    # Keep historical snapshots importable so the durable ledger remains
    # continuous across strategy renames.
    "exacta_niche_hamanako14": "hamanako_14_exa",
}


def canonical_strategy_key(strategy_key: str) -> str:
    return STRATEGY_KEY_ALIASES.get(str(strategy_key or ""), str(strategy_key or ""))


def _is_excluded_history_label(label: Any) -> bool:
    text = str(label or "")
    return "女性" in text and ("除外" in text or "混在" in text)


def ensure_roi_race_history_table(conn: Any) -> None:
    """Create the durable per-race ROI ledger for SQLite and PostgreSQL."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS roi_race_history (
            race_date TEXT NOT NULL,
            race_id TEXT NOT NULL,
            strategy_key TEXT NOT NULL,
            strategy_label TEXT,
            bet_json TEXT NOT NULL,
            stake_amount INTEGER NOT NULL,
            payout_amount INTEGER NOT NULL DEFAULT 0,
            is_hit INTEGER NOT NULL DEFAULT 0,
            is_settled INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            source_cache_key TEXT NOT NULL,
            source_cache_version TEXT,
            strategy_signature TEXT,
            snapshot_computed_at TEXT,
            capture_quality TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (race_id, strategy_key)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_roi_race_history_date "
        "ON roi_race_history(race_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_roi_race_history_strategy_date "
        "ON roi_race_history(strategy_key, race_date)"
    )


def replace_roi_history_snapshot(
    conn: Any,
    payload: dict[str, Any],
    *,
    source_cache_key: str,
    capture_quality: str,
    adopted_keys: Iterable[str],
    bet_unit_map: dict[str, int],
    parse_bets: Callable[[dict[str, Any]], list[tuple[str, str]]],
    strategy_signature: str,
) -> int:
    """Replace one date's ledger with the selected races in one saved snapshot."""
    race_date = str(payload.get("date") or "")
    signals = payload.get("signals")
    if not race_date or not isinstance(signals, dict):
        return 0

    ensure_roi_race_history_table(conn)
    adopted = set(adopted_keys)
    now_iso = datetime.now().isoformat(timespec="seconds")
    payload_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:20]
    rows: list[tuple[Any, ...]] = []

    for rid, signal in signals.items():
        if not isinstance(signal, dict):
            continue
        l4 = signal.get("l4") or {}
        if not isinstance(l4, dict):
            continue
        if l4.get("is_reference"):
            continue
        if int((signal or {}).get("n_female") or 0) > 0 and not l4.get(
            "allow_female_market_signal"
        ):
            continue
        if l4.get("is_female_present") and not l4.get("allow_female_market_signal"):
            continue
        level_candidates = [str(l4.get("level") or "")]
        level_candidates.extend(str(value) for value in (l4.get("matched_levels") or []) if value)
        strategy_key = next(
            (
                canonical_strategy_key(key) for key in level_candidates
                if canonical_strategy_key(key) in adopted and not key.startswith("morning_watch_")
            ),
            "",
        )
        if not strategy_key:
            continue
        if l4.get("is_after_exhibition_out") or l4.get("start_prediction_filter_status") == "failed":
            continue
        race_id = str(signal.get("race_id") or rid or "")
        bets = parse_bets(l4)
        if not race_id or not bets:
            continue

        payout = 0
        for bet_type, combination in bets:
            payout_row = conn.execute(
                "SELECT payout FROM race_payouts "
                "WHERE race_id = ? AND bet_type = ? AND combination = ? "
                "ORDER BY payout DESC LIMIT 1",
                (race_id, bet_type, combination),
            ).fetchone()
            payout += int(payout_row[0] or 0) if payout_row else 0
        settled_row = conn.execute(
            "SELECT 1 FROM race_results WHERE race_id = ? AND finishing_position = 1 LIMIT 1",
            (race_id,),
        ).fetchone()
        if not settled_row:
            settled_row = conn.execute(
                "SELECT 1 FROM race_payouts WHERE race_id = ? LIMIT 1", (race_id,)
            ).fetchone()
        is_settled = 1 if settled_row else 0
        default_stake = 100 * max(1, len(bets))
        stake = max(default_stake, int(bet_unit_map.get(strategy_key, default_stake)))
        rows.append(
            (
                race_date,
                race_id,
                strategy_key,
                str(l4.get("label") or l4.get("name") or strategy_key),
                json.dumps(
                    [{"bet_type": bet_type, "combination": combination} for bet_type, combination in bets],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                stake,
                payout if is_settled else 0,
                1 if is_settled and payout > 0 else 0,
                is_settled,
                source_cache_key,
                str(payload.get("cache_version") or ""),
                strategy_signature,
                str(payload.get("computed_at") or ""),
                capture_quality,
                payload_hash,
                now_iso,
            )
        )

    if rows:
        conn.executemany(
            """
            INSERT INTO roi_race_history (
                race_date, race_id, strategy_key, strategy_label, bet_json,
                stake_amount, payout_amount, is_hit, is_settled, is_active,
                source_cache_key, source_cache_version, strategy_signature,
                snapshot_computed_at, capture_quality, payload_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (race_id, strategy_key) DO UPDATE SET
                race_date = excluded.race_date,
                strategy_label = excluded.strategy_label,
                bet_json = excluded.bet_json,
                stake_amount = excluded.stake_amount,
                payout_amount = excluded.payout_amount,
                is_hit = excluded.is_hit,
                is_settled = excluded.is_settled,
                is_active = 1,
                source_cache_key = excluded.source_cache_key,
                source_cache_version = excluded.source_cache_version,
                strategy_signature = excluded.strategy_signature,
                snapshot_computed_at = excluded.snapshot_computed_at,
                capture_quality = excluded.capture_quality,
                payload_hash = excluded.payload_hash,
                updated_at = excluded.updated_at
            """,
            rows,
        )
        # Preserve old candidates for audit, but exclude candidates absent from
        # the successfully stored replacement snapshot from operational ROI.
        conn.execute(
            "UPDATE roi_race_history SET is_active = 0 "
            "WHERE race_date = ? AND payload_hash <> ?",
            (race_date, payload_hash),
        )
    else:
        # Zero candidates is a valid completed snapshot. Keep the rows and only
        # retire them from the active operational view.
        conn.execute(
            "UPDATE roi_race_history SET is_active = 0 WHERE race_date = ?",
            (race_date,),
        )
    conn.commit()
    return len(rows)


def load_roi_history_daily(
    conn: Any, from_date: str, to_date: str, adopted_keys: Iterable[str]
) -> dict[str, dict[str, dict[str, int]]]:
    """Return settled ledger totals grouped by date and strategy."""
    ensure_roi_race_history_table(conn)
    adopted = set(adopted_keys)
    grouped: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"bets": 0, "hits": 0, "pay": 0, "stake": 0})
    )
    rows = conn.execute(
        """
        SELECT race_date, strategy_key, strategy_label, stake_amount, payout_amount, is_hit
          FROM roi_race_history
         WHERE race_date BETWEEN ? AND ? AND is_settled = 1 AND is_active = 1
         ORDER BY race_date, race_id
        """,
        (from_date, to_date),
    ).fetchall()
    for race_date, strategy_key, strategy_label, stake, payout, is_hit in rows:
        if _is_excluded_history_label(strategy_label):
            continue
        key = str(strategy_key)
        if key not in adopted:
            continue
        item = grouped[str(race_date)][key]
        item["bets"] += 1
        item["hits"] += int(is_hit or 0)
        item["pay"] += int(payout or 0)
        item["stake"] += int(stake or 0)
    return {date_key: dict(values) for date_key, values in grouped.items()}


def load_roi_history_races(
    conn: Any,
    from_date: str,
    to_date: str,
    adopted_keys: Iterable[str],
    *,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return settled active ROI ledger rows for the history screen."""
    ensure_roi_race_history_table(conn)
    adopted = set(adopted_keys)
    rows = conn.execute(
        """
        SELECT h.race_date,
               h.race_id,
               h.strategy_key,
               h.strategy_label,
               h.bet_json,
               h.stake_amount,
               h.payout_amount,
               h.is_hit,
               h.capture_quality,
               s.name AS stadium_name,
               r.race_number,
               r.race_closed_at
          FROM roi_race_history h
          LEFT JOIN races r ON r.race_id = h.race_id
          LEFT JOIN stadiums s ON s.stadium_number = r.stadium_number
         WHERE h.race_date BETWEEN ? AND ?
           AND h.is_settled = 1
           AND h.is_active = 1
         ORDER BY h.race_date DESC, h.race_id, h.strategy_key
         LIMIT ?
        """,
        (from_date, to_date, int(limit)),
    ).fetchall()

    result: list[dict[str, Any]] = []
    for row in rows:
        if _is_excluded_history_label(row[3]):
            continue
        key = str(row[2])
        if key not in adopted:
            continue
        stake = int(row[5] or 0)
        payout = int(row[6] or 0)
        try:
            bets_raw = json.loads(row[4] or "[]")
        except Exception:
            bets_raw = []
        bets = []
        if isinstance(bets_raw, list):
            for bet in bets_raw:
                if isinstance(bet, dict):
                    bet_type = str(bet.get("bet_type") or "")
                    combination = str(bet.get("combination") or "")
                    bets.append(
                        f"{bet_type} {combination}".strip()
                        if bet_type
                        else combination
                    )
        result.append(
            {
                "race_date": str(row[0]),
                "race_id": str(row[1]),
                "strategy_key": key,
                "strategy_label": str(row[3] or key),
                "bet": " / ".join(x for x in bets if x) or "-",
                "stake": stake,
                "payout": payout,
                "profit": payout - stake,
                "recovery": round((payout / stake) * 100, 1) if stake > 0 else None,
                "is_hit": bool(row[7]),
                "capture_quality": str(row[8] or ""),
                "stadium_name": str(row[9] or ""),
                "race_number": int(row[10] or 0) if row[10] is not None else None,
                "closed_at": str(row[11] or ""),
            }
        )
    return result
