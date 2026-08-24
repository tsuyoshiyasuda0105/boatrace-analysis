"""
DB 接続ヘルパー (SQLite + PostgreSQL 両対応)

ローカル開発: SQLite (config.DB_PATH)
Render 本番:  PostgreSQL (Supabase 等、env DATABASE_URL)

接続選択ロジック:
  - 環境変数 DATABASE_URL が postgres:// or postgresql:// で始まる → psycopg を使用
  - それ以外 (未設定 or sqlite path) → sqlite3 を使用

使う側は `connect()` の戻り値の execute / executemany / commit / close
を sqlite3 互換の感覚で使える (psycopg3 でも同等のメソッドが揃っている)。

Postgres 専用処理:
  - PRAGMA は SQLite 専用なので psycopg では skip
  - 自動コミットは ON (sqlite3 と同じ挙動)
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
import time
from typing import Optional, Union

import config


logger = logging.getLogger(__name__)
_PG_POOL = None
_PG_POOL_LOCK = threading.Lock()
_TRANSIENT_DB_RETRY_STATE = threading.local()
_PG_POOL_EXHAUSTED_SINCE = None
_PG_POOL_EXHAUSTION_FAILURES = 0
_PG_POOL_LAST_REBUILD_AT = None
_SQL_MEASUREMENT = threading.local()
_WEB_REQUEST_DB_BUDGET = threading.local()
_PG_POOL_LIFECYCLE_LOCK = threading.Lock()
_PG_POOL_LIFECYCLE_EVENTS: list[dict[str, object]] = []
_PG_POOL_ACTIVE_CHECKOUTS = 0
_PG_POOL_RECLAIMED_BY_GC = 0
_PG_POOL_PEAK_CHECKOUTS = 0

_DEFAULT_CONNECT_RETRY_DELAYS = (0.2, 0.5)
_MAX_CONNECT_RETRIES = 2
_MAX_CONNECT_RETRY_DELAY_SEC = 0.5
_DEFAULT_WEB_CHECKOUT_BUDGET_SEC = 10.0
_MAX_WEB_CHECKOUT_BUDGET_SEC = 10.0
_POOL_LIFECYCLE_BUFFER_MAX = 100
_DEFAULT_POOL_EXHAUSTION_SEC = 90.0
_DEFAULT_POOL_REBUILD_COOLDOWN_SEC = 60.0


class ConnectionCheckoutBudgetExceeded(TimeoutError):
    """A Web request exhausted its aggregate shared-pool checkout budget."""


def begin_web_request_db_budget(total_sec: Optional[float] = None) -> None:
    """Start one aggregate checkout budget for the current Web request."""
    if total_sec is None:
        try:
            total_sec = float(
                os.getenv(
                    "BOATRACE_WEB_DB_CHECKOUT_BUDGET_SEC",
                    str(_DEFAULT_WEB_CHECKOUT_BUDGET_SEC),
                )
            )
        except (TypeError, ValueError):
            total_sec = _DEFAULT_WEB_CHECKOUT_BUDGET_SEC
    bounded = max(1.0, min(_MAX_WEB_CHECKOUT_BUDGET_SEC, float(total_sec)))
    _WEB_REQUEST_DB_BUDGET.deadline = time.monotonic() + bounded


def end_web_request_db_budget() -> None:
    _WEB_REQUEST_DB_BUDGET.deadline = None


def _web_request_db_budget_remaining() -> Optional[float]:
    deadline = getattr(_WEB_REQUEST_DB_BUDGET, "deadline", None)
    if deadline is None:
        return None
    return max(0.0, float(deadline) - time.monotonic())


def _pool_timeout_seconds() -> float:
    try:
        value = float(os.getenv("BOATRACE_DB_POOL_TIMEOUT_SEC", "5"))
    except (TypeError, ValueError):
        value = 5.0
    return max(1.0, value)


def _safe_pool_stats(pool) -> dict[str, object]:
    try:
        raw = pool.get_stats()
    except Exception:
        return {}
    allowed = {
        "pool_size",
        "pool_available",
        "requests_waiting",
        "requests_num",
        "requests_queued",
        "requests_errors",
    }
    return {key: raw[key] for key in allowed if key in raw}


def _append_pool_lifecycle_event(event: dict[str, object]) -> None:
    with _PG_POOL_LIFECYCLE_LOCK:
        if len(_PG_POOL_LIFECYCLE_EVENTS) >= _POOL_LIFECYCLE_BUFFER_MAX:
            del _PG_POOL_LIFECYCLE_EVENTS[0]
        _PG_POOL_LIFECYCLE_EVENTS.append(event)


def _note_pool_acquired(pool, wait_ms: float) -> int:
    global _PG_POOL_ACTIVE_CHECKOUTS, _PG_POOL_PEAK_CHECKOUTS
    with _PG_POOL_LIFECYCLE_LOCK:
        _PG_POOL_ACTIVE_CHECKOUTS += 1
        _PG_POOL_PEAK_CHECKOUTS = max(
            _PG_POOL_PEAK_CHECKOUTS, _PG_POOL_ACTIVE_CHECKOUTS
        )
        active = _PG_POOL_ACTIVE_CHECKOUTS
        peak = _PG_POOL_PEAK_CHECKOUTS
    _append_pool_lifecycle_event(
        {
            "event": "checkout",
            "at_epoch": round(time.time(), 3),
            "wait_ms": round(max(0.0, wait_ms), 1),
            "concurrent_acquired": active,
            "peak_concurrent": peak,
            **_safe_pool_stats(pool),
        }
    )
    return active


def _note_pool_released(pool, hold_ms: float) -> None:
    global _PG_POOL_ACTIVE_CHECKOUTS
    with _PG_POOL_LIFECYCLE_LOCK:
        _PG_POOL_ACTIVE_CHECKOUTS = max(0, _PG_POOL_ACTIVE_CHECKOUTS - 1)
        active = _PG_POOL_ACTIVE_CHECKOUTS
        peak = _PG_POOL_PEAK_CHECKOUTS
    _append_pool_lifecycle_event(
        {
            "event": "release",
            "at_epoch": round(time.time(), 3),
            "hold_ms": round(max(0.0, hold_ms), 1),
            "concurrent_acquired": active,
            "peak_concurrent": peak,
            **_safe_pool_stats(pool),
        }
    )


def _note_pool_checkout_failed(pool, wait_ms: float, exc: BaseException) -> None:
    with _PG_POOL_LIFECYCLE_LOCK:
        active = _PG_POOL_ACTIVE_CHECKOUTS
        peak = _PG_POOL_PEAK_CHECKOUTS
    _append_pool_lifecycle_event(
        {
            "event": "checkout_failed",
            "at_epoch": round(time.time(), 3),
            "wait_ms": round(max(0.0, wait_ms), 1),
            "error_type": type(exc).__name__,
            "concurrent_acquired": active,
            "peak_concurrent": peak,
            **_safe_pool_stats(pool),
        }
    )


def pg_pool_report() -> dict[str, object]:
    """接続プールの今の姿を読み取り専用で返す (調査用)。

    プロセスの環境変数や接続の状態を一切書き換えないこと。以前あった調査用
    エンドポイントは os.environ["BOATRACE_TASK_TRIGGER"] を一時的に書き換えて
    いたが、これは worker 内の全スレッドに効いてしまい、無関係なリクエストの
    接続の取り方まで変えてしまう危険があった (2026-08-24 に撤去)。

    gunicorn は複数 worker で動くので、どの worker が答えたか分かるように pid を
    含める。片方の worker だけ不調、という状態を外から見分けるのに要る。
    """
    report: dict[str, object] = {
        "pid": os.getpid(),
        "pool_exists": _PG_POOL is not None,
        "configured": {
            "timeout_sec": _pool_timeout_seconds(),
            "connect_timeout_sec": _pg_connect_timeout_seconds(),
        },
    }
    pool = _PG_POOL
    if pool is not None:
        report["stats"] = _safe_pool_stats(pool)
        for name in ("min_size", "max_size", "max_waiting", "max_idle", "max_lifetime"):
            value = getattr(pool, name, None)
            if value is not None:
                report["configured"][name] = value
    with _PG_POOL_LIFECYCLE_LOCK:
        report["active_checkouts"] = _PG_POOL_ACTIVE_CHECKOUTS
        report["peak_checkouts"] = _PG_POOL_PEAK_CHECKOUTS
        report["reclaimed_by_gc"] = _PG_POOL_RECLAIMED_BY_GC
        report["recent_events"] = list(_PG_POOL_LIFECYCLE_EVENTS)[-10:]
    report["holders"] = _describe_connection_holders()
    report["threads"] = sorted(t.name for t in threading.enumerate())[:20]
    return report


def _describe_connection_holders() -> dict[str, object]:
    """まだ接続を握っている _PgConnection と、それを参照している側を数える。

    2026-08-24 実障害の最後の未解明点。Postgres 側では接続が idle で生きている
    のに、プールは pool_available=0 のまま。つまりアプリが借りたまま返していない
    のだが、こちらの貸出カウンタは 0 を指し (下限で丸められる)、GC の回収も
    0 件だった。オブジェクトが生きたまま誰かに参照され続けている、という仮説を
    確かめるには実物を数えるしかない。読み取り専用で、調査用エンドポイント
    からのみ呼ぶ。
    """
    import gc

    out: dict[str, object] = {}
    try:
        live = [
            obj
            for obj in gc.get_objects()
            if type(obj).__name__ == "_PgConnection"
        ]
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}

    holding = [o for o in live if getattr(o, "_conn", None) is not None]
    out["live_objects"] = len(live)
    out["still_holding"] = len(holding)

    referrer_types: dict[str, int] = {}
    for obj in holding[:10]:
        try:
            for ref in gc.get_referrers(obj):
                name = type(ref).__name__
                if name == "list" and len(ref) < 40:
                    name = f"list[{','.join(sorted({type(x).__name__ for x in ref}))[:60]}]"
                referrer_types[name] = referrer_types.get(name, 0) + 1
        except Exception:  # noqa: BLE001
            continue
    out["referrers"] = dict(
        sorted(referrer_types.items(), key=lambda kv: -kv[1])[:12]
    )
    return out


def consume_pg_pool_lifecycle_events() -> list[dict[str, object]]:
    """Return and clear bounded non-secret shared-pool lifecycle measurements."""
    with _PG_POOL_LIFECYCLE_LOCK:
        events = list(_PG_POOL_LIFECYCLE_EVENTS)
        _PG_POOL_LIFECYCLE_EVENTS.clear()
    return events


def reset_sql_count() -> None:
    _SQL_MEASUREMENT.count = 0


def get_sql_count() -> int:
    return int(getattr(_SQL_MEASUREMENT, "count", 0) or 0)


def _count_sql() -> None:
    if os.getenv("BOATRACE_MEASURE_SQL", "").strip().lower() in {
        "1", "true", "yes", "on",
    }:
        _SQL_MEASUREMENT.count = get_sql_count() + 1


def _sqlstate_from_exception(exc: BaseException) -> str:
    return str(
        getattr(exc, "sqlstate", "")
        or getattr(exc, "pgcode", "")
        or ""
    ).upper()


def is_transient_db_error(exc: BaseException) -> bool:
    """Return whether *exc* is a short-lived connection acquisition failure.

    Authentication/configuration failures are deliberately excluded.  The
    message fallback is only for driver/wrapper exceptions that do not expose
    SQLSTATE (notably psycopg_pool.PoolTimeout and some network timeouts).
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        state = _sqlstate_from_exception(current)
        if state.startswith(("28", "3D")):
            return False
        if state.startswith("08") or state in {"57P01", "57P02", "57P03"}:
            return True
        try:
            from psycopg_pool import PoolTimeout, TooManyRequests

            if isinstance(current, (PoolTimeout, TooManyRequests)):
                return True
        except (ImportError, TypeError):
            pass
        if isinstance(current, (TimeoutError, ConnectionError)):
            return True
        message = str(current).lower()
        if any(
            marker in message
            for marker in (
                "pooltimeout",
                "pool timeout",
                "couldn't get a connection",
                "connection timed out",
                "connection timeout",
                "connection refused",
                "connection reset",
                "server closed the connection",
                "the database system is starting up",
                "the database system is shutting down",
            )
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _is_pool_queue_overflow(exc: BaseException) -> bool:
    try:
        from psycopg_pool import TooManyRequests

        return isinstance(exc, TooManyRequests)
    except (ImportError, TypeError):
        return False


def _connect_retry_delays() -> tuple[float, ...]:
    try:
        retries = int(os.getenv("BOATRACE_DB_CONNECT_RETRIES", "2"))
    except (TypeError, ValueError):
        retries = 2
    retries = max(0, min(_MAX_CONNECT_RETRIES, retries))
    raw = os.getenv("BOATRACE_DB_CONNECT_RETRY_DELAYS_SEC", "0.2,0.5")
    parsed: list[float] = []
    for item in str(raw).split(","):
        try:
            parsed.append(
                max(0.0, min(_MAX_CONNECT_RETRY_DELAY_SEC, float(item.strip())))
            )
        except (TypeError, ValueError):
            continue
    if not parsed:
        parsed = list(_DEFAULT_CONNECT_RETRY_DELAYS)
    while len(parsed) < retries:
        parsed.append(parsed[-1])
    return tuple(parsed[:retries])


def consume_transient_db_retry_event() -> Optional[dict[str, object]]:
    """Return and clear the retry event for the current thread, if any."""
    event = getattr(_TRANSIENT_DB_RETRY_STATE, "event", None)
    _TRANSIENT_DB_RETRY_STATE.event = None
    return dict(event) if isinstance(event, dict) else None


def _acquire_pg_connection(dsn: str, *, direct: bool, pool=None):
    delays = _connect_retry_delays()
    _TRANSIENT_DB_RETRY_STATE.event = None
    for attempt in range(len(delays) + 1):
        remaining = None if direct else _web_request_db_budget_remaining()
        if remaining is not None and remaining <= 0:
            raise ConnectionCheckoutBudgetExceeded(
                "shared database pool is busy; request checkout budget exhausted"
            )
        try:
            if direct:
                conn = _open_direct_pg_connection(dsn)
            elif remaining is None:
                conn = pool.getconn()
            else:
                conn = pool.getconn(timeout=min(_pool_timeout_seconds(), remaining))
            if not direct:
                # 記録の失敗で except に落ちると、取得済みの接続を握ったまま
                # 再試行して 1 本捨てることになる。記録は握り潰す。
                try:
                    _note_pg_pool_checkout_success(pool)
                except Exception:
                    logger.warning("pool checkout bookkeeping failed", exc_info=True)
            return conn
        except Exception as exc:
            # max_waiting is the fail-fast boundary. Do not turn an immediate
            # queue rejection into the normal connection retry backoff; the Web
            # fallback layer should receive it immediately.
            if (
                _is_pool_queue_overflow(exc)
                or not is_transient_db_error(exc)
                or attempt >= len(delays)
            ):
                raise
            delay = delays[attempt]
            _TRANSIENT_DB_RETRY_STATE.event = {
                "retry_count": attempt + 1,
                "error_type": type(exc).__name__,
                "last_error": str(exc)[:200],
                "direct": bool(direct),
            }
            logger.warning(
                "transient postgres connection failure; retry=%d/%d delay=%.3fs type=%s",
                attempt + 1,
                len(delays),
                delay,
                type(exc).__name__,
            )
            remaining = None if direct else _web_request_db_budget_remaining()
            if remaining is not None:
                if remaining <= 0:
                    raise ConnectionCheckoutBudgetExceeded(
                        "shared database pool is busy; request checkout budget exhausted"
                    ) from exc
                delay = min(delay, remaining)
            time.sleep(delay)


def _pool_watchdog_seconds(env_name: str, default: float) -> float:
    try:
        value = float(os.getenv(env_name, str(default)))
    except (TypeError, ValueError):
        value = default
    # A bad production setting must not turn a momentary saturation into a
    # rebuild loop. Tests pass explicit durations to the watchdog helper.
    return max(30.0, value)


def _note_pg_pool_checkout_success(pool) -> None:
    """Clear a pending exhaustion window after any successful checkout."""
    global _PG_POOL_EXHAUSTED_SINCE, _PG_POOL_EXHAUSTION_FAILURES
    with _PG_POOL_LOCK:
        if _PG_POOL is pool:
            _PG_POOL_EXHAUSTED_SINCE = None
            _PG_POOL_EXHAUSTION_FAILURES = 0


def _maybe_rebuild_exhausted_pg_pool(
    pool,
    stats: dict[str, object],
    *,
    now: Optional[float] = None,
    exhaustion_sec: Optional[float] = None,
    cooldown_sec: Optional[float] = None,
) -> bool:
    """Retire a persistently exhausted Web pool after failed checkouts.

    This is called only from the final checkout failure path. A rebuild needs
    at least two failed observations, continuous zero availability with queued
    callers, the configured duration, and an elapsed rebuild cooldown.
    """
    global _PG_POOL, _PG_POOL_EXHAUSTED_SINCE
    global _PG_POOL_EXHAUSTION_FAILURES, _PG_POOL_LAST_REBUILD_AT

    if os.getenv("BOATRACE_TASK_TRIGGER", "").strip():
        return False
    try:
        available = int(stats.get("pool_available", -1))
        waiting = int(stats.get("requests_waiting", 0))
    except (TypeError, ValueError):
        available, waiting = -1, 0
    if available != 0 or waiting <= 0:
        with _PG_POOL_LOCK:
            if _PG_POOL is pool:
                _PG_POOL_EXHAUSTED_SINCE = None
                _PG_POOL_EXHAUSTION_FAILURES = 0
        return False

    observed_at = time.monotonic() if now is None else now
    required_sec = (
        _pool_watchdog_seconds(
            "BOATRACE_DB_POOL_EXHAUSTION_SEC", _DEFAULT_POOL_EXHAUSTION_SEC
        )
        if exhaustion_sec is None
        else max(0.0, exhaustion_sec)
    )
    required_cooldown = (
        _pool_watchdog_seconds(
            "BOATRACE_DB_POOL_REBUILD_COOLDOWN_SEC",
            _DEFAULT_POOL_REBUILD_COOLDOWN_SEC,
        )
        if cooldown_sec is None
        else max(0.0, cooldown_sec)
    )

    with _PG_POOL_LOCK:
        if _PG_POOL is not pool:
            return False
        if _PG_POOL_EXHAUSTED_SINCE is None:
            _PG_POOL_EXHAUSTED_SINCE = observed_at
            _PG_POOL_EXHAUSTION_FAILURES = 1
            logger.warning(
                "postgres pool exhaustion detected; waiting=%d available=%d",
                waiting,
                available,
            )
            return False

        _PG_POOL_EXHAUSTION_FAILURES += 1
        exhausted_for = max(0.0, observed_at - _PG_POOL_EXHAUSTED_SINCE)
        cooldown_elapsed = (
            _PG_POOL_LAST_REBUILD_AT is None
            or observed_at - _PG_POOL_LAST_REBUILD_AT >= required_cooldown
        )
        if (
            _PG_POOL_EXHAUSTION_FAILURES < 2
            or exhausted_for < required_sec
            or not cooldown_elapsed
        ):
            return False

        logger.error(
            "rebuilding exhausted postgres pool; exhausted_sec=%.1f failures=%d "
            "waiting=%d cooldown_sec=%.1f",
            exhausted_for,
            _PG_POOL_EXHAUSTION_FAILURES,
            waiting,
            required_cooldown,
        )
        _PG_POOL = None
        _PG_POOL_EXHAUSTED_SINCE = None
        _PG_POOL_EXHAUSTION_FAILURES = 0
        _PG_POOL_LAST_REBUILD_AT = observed_at
        try:
            pool.close()
        except Exception as exc:
            logger.warning(
                "retired postgres pool close failed; type=%s", type(exc).__name__
            )
        return True


def _is_postgres_url(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _normalize_pg_url(url: str) -> str:
    """psycopg3 は postgres:// を拒否するので postgresql:// に正規化。
    Supabase が pooled connection で sslmode を求めるため、無ければ追加。"""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"
    return url


def targets_postgres(db_path: Optional[str] = None) -> bool:
    """Return True when connect() would target Postgres/Supabase."""
    if db_path:
        return False
    db_url = os.getenv("DATABASE_URL", "").strip()
    return bool(db_url and _is_postgres_url(db_url))


def assert_safe_production_write(
    *,
    action: str,
    db_path: Optional[str] = None,
    allow_env_var: str = "BOATRACE_ALLOW_PROD_WRITE",
) -> None:
    """Refuse local writes to production Postgres unless explicitly allowed.

    Accident-related batch jobs must not overwrite the production Supabase data
    from a local shell by accident. Render cron remains allowed, and callers can
    still target a local SQLite path explicitly.
    """
    if db_path or not targets_postgres(db_path):
        return
    if os.getenv("RENDER", "").strip():
        return
    if os.getenv(allow_env_var, "").strip() == "1":
        return
    raise RuntimeError(
        f"{action} refused: local process would write to production Postgres via DATABASE_URL. "
        f"Use local SQLite, run on Render, or set {allow_env_var}=1 only for an intentional emergency override."
    )


def _placeholder_pg(sql: str, *, escape_percent: bool = False) -> str:
    """SQLite の `?` プレースホルダを Postgres の `%s` に変換。
    クォート内の '?' は触らない (素朴な実装)。"""
    out = []
    in_str = False
    quote = ""
    i = 0
    while i < len(sql):
        ch = sql[i]
        if not in_str and ch in ("'", '"'):
            in_str = True
            quote = ch
            out.append(ch)
        elif in_str and ch == quote:
            out.append(ch)
            if i + 1 < len(sql) and sql[i + 1] == quote:
                out.append(sql[i + 1])
                i += 1
            else:
                in_str = False
        elif not in_str and ch == "?":
            out.append("%s")
        elif escape_percent and ch == "%":
            if not in_str and i + 1 < len(sql) and sql[i + 1] in ("s", "%"):
                out.extend((ch, sql[i + 1]))
                i += 1
            elif in_str and i + 1 < len(sql) and sql[i + 1] == "%":
                out.extend((ch, sql[i + 1]))
                i += 1
            else:
                out.append("%%")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# schema.sql で定義された各テーブルの主キー (UPSERT 変換用)
_TABLE_PRIMARY_KEYS = {
    # kachisuji デルタ適用の記帳 (delta_transport は素の sqlite3 で書くが
    # 静的パリティ検査のため登録する)
    "applied_deltas": ["name"],
    "stadiums": ["stadium_number"],
    "racers": ["racer_number"],
    "racer_period_stats": ["racer_number", "period_year", "period_half"],
    "races": ["race_id"],
    "race_entries": ["race_id", "boat_number"],
    "race_previews": ["race_id", "boat_number"],
    "race_parts": ["race_id", "boat_number", "part_code"],
    # sync_to_supabase.py が動的 SQL で書く (静的 grep に映らない) ので注意。
    # 2026-08-15 の夜間 sync がここの欠落で停止した (P1-3 の strict guard が検出)。
    "race_tides": ["race_id"],
    "race_original_exhibitions": ["race_id", "boat_number", "source_name"],
    "race_results": ["race_id", "boat_number"],
    "race_payouts": ["race_id", "bet_type", "combination"],
    "odds_trifecta": ["race_id", "combination", "recorded_at"],
    "predictions": ["race_id", "boat_number", "model_version"],
    "value_bets": ["race_id", "bet_type", "combination", "model_version"],
    "l4_daily_summary": ["date"],
    "l4_daily_stats_cache": ["race_date"],
    "course1_stats_cache": ["racer_number", "as_of_date"],
    "decay_factor": ["bucket"],
    "paper_trades": ["id"],
    "alert_sent": ["email_hash", "race_id", "alert_type"],
    "incident_log": ["incident_id"],
    "roi_race_history": ["race_id", "strategy_key"],
    "derived_start_stats": ["race_id", "boat_number"],
    "racer_accident_point_rules": ["rule_version", "event_code", "applies_from"],
    "racer_accident_events": ["race_id", "racer_number", "event_code", "rule_version"],
    "racer_accident_kraw_unmatched": ["file_name", "line_number", "rule_version"],
    # 本番 Postgres の実 PK は period_end を含む。ON CONFLICT 変換は Postgres 専用
    # なので、ローカル SQLite ではなく本番 Postgres の PK に一致させること
    # (period_end を外すと ON CONFLICT が制約に一致せず事故率パイプラインが壊れる)。
    "racer_accident_period_stats": ["racer_number", "period_year", "period_half", "period_end", "rule_version", "source_kind"],
    "racer_accident_period_adjustments": ["racer_number", "period_start", "period_end", "rule_version", "source_kind"],
    "racer_accident_external_snapshots": ["snapshot_date", "racer_number", "source_kind"],
    "racer_accident_rank_snapshots": ["period_start", "racer_number"],
    "racer_entry_change_snapshots": ["snapshot_date", "racer_number"],
    "motor_preinspection_stats": ["stadium_number", "race_date", "source_name", "motor_number", "racer_number"],
}


def _unquote_identifier(value: str) -> str:
    """`"stadiums"` → `stadiums`。sync_to_supabase 等がクォートで囲んだ識別子に対応。"""
    return value.strip().strip('"')


def _build_upsert(table: str, columns: list[str], kind: str) -> str:
    """ON CONFLICT (pk) DO UPDATE SET col=EXCLUDED.col の SQL 末尾を生成。"""
    kind = kind.upper()
    if kind == "IGNORE":
        return " ON CONFLICT DO NOTHING"
    if kind != "REPLACE":
        raise ValueError(f"Unsupported SQLite INSERT OR kind: {kind}")
    # 識別子がダブルクォートで囲まれていても主キー照合できるよう正規化する
    # (2026-08-20: sync_to_supabase の識別子クォート化で翻訳が壊れた回帰の修理)
    table_key = _unquote_identifier(table).lower()
    columns = [_unquote_identifier(c) for c in columns]
    pk = _TABLE_PRIMARY_KEYS.get(table_key)
    if not pk:
        # REPLACE must never silently degrade to DO NOTHING.
        raise ValueError(
            f"INSERT OR REPLACE target table '{table_key}' is missing from "
            "_TABLE_PRIMARY_KEYS"
        )
    non_pk = [c for c in columns if c not in pk]
    if not non_pk:
        # 全列が主キー → DO NOTHING
        return f" ON CONFLICT ({', '.join(pk)}) DO NOTHING"
    set_clause = ", ".join(f"{c}=EXCLUDED.{c}" for c in non_pk)
    return f" ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {set_clause}"


def _strip_trailing_line_comment(sql: str) -> str:
    """Remove only a terminal SQL `--` comment."""
    in_str = False
    quote = ""
    i = 0
    while i < len(sql):
        ch = sql[i]
        if in_str:
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 2
                    continue
                in_str = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_str = True
            quote = ch
            i += 1
            continue
        if ch == "-" and i + 1 < len(sql) and sql[i + 1] == "-":
            newline = sql.find("\n", i + 2)
            if newline < 0 or not sql[newline + 1:].strip():
                return sql[:i].rstrip()
            i = newline + 1
            continue
        i += 1
    return sql.rstrip()


_INSERT_PATTERN = re.compile(
    # テーブル名は素の識別子 (stadiums) でもクォート付き ("stadiums") でも受ける
    r'\bINSERT\s+OR\s+(REPLACE|IGNORE)\s+INTO\s+("?\w+"?)\s*\(([^)]+)\)',
    re.IGNORECASE | re.DOTALL,
)


def _rewrite_sqlite_specific(sql: str) -> str:
    """SQLite 固有の構文を Postgres 互換に書き換え。
    単一文 (1つの INSERT 文) を想定。

    - INSERT OR REPLACE INTO t (cols) ... → INSERT INTO + ON CONFLICT (pk) DO UPDATE SET
    - INSERT OR IGNORE INTO t (cols) ...  → INSERT INTO + ON CONFLICT DO NOTHING
    """
    m = _INSERT_PATTERN.search(sql)
    if not m:
        return sql
    kind = m.group(1).upper()
    table = m.group(2)
    cols_raw = m.group(3)
    cols = [c.strip() for c in cols_raw.split(",") if c.strip()]
    head = f"INSERT INTO {table} ({cols_raw})"
    tail = _build_upsert(table, cols, kind)
    rewritten = sql[:m.start()] + head + sql[m.end():]
    # 末尾セミコロンの前に ON CONFLICT を挿入
    rewritten = _strip_trailing_line_comment(rewritten)
    if rewritten.endswith(";"):
        rewritten = rewritten[:-1].rstrip() + tail + ";"
    else:
        rewritten = rewritten + tail
    return rewritten


def _configure_pg_connection(conn) -> None:
    # Configure each physical connection once before the pool serves it.
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            trigger = os.getenv("BOATRACE_TASK_TRIGGER", "").strip().lower()
            default_statement_timeout = "0" if trigger else "8000"
            statement_timeout = max(
                0,
                int(os.getenv("BOATRACE_DB_STATEMENT_TIMEOUT_MS", default_statement_timeout)),
            )
            cur.execute("SET max_parallel_workers_per_gather = 0")
            cur.execute("SET work_mem = '16MB'")
            cur.execute(f"SET statement_timeout = {statement_timeout}")
            cur.execute("SET lock_timeout = '3s'")
            cur.execute("SET idle_in_transaction_session_timeout = '15s'")
            cur.execute("SET enable_hashjoin = on")
            cur.execute("SET enable_mergejoin = off")
    except Exception:
        pass


_PG_POOL_CHECKER_STARTED = False
_DEFAULT_POOL_CHECK_INTERVAL_SEC = 45.0


def _pool_check_interval_seconds() -> float:
    try:
        return max(
            5.0,
            float(
                os.getenv(
                    "BOATRACE_DB_POOL_CHECK_INTERVAL_SEC",
                    str(_DEFAULT_POOL_CHECK_INTERVAL_SEC),
                )
            ),
        )
    except (TypeError, ValueError):
        return _DEFAULT_POOL_CHECK_INTERVAL_SEC


def _start_pool_health_checker(pool) -> None:
    """遊休接続の生死を裏で確かめ、死んでいれば張り替える。

    2026-08-24 実障害の決め手。psycopg は貸し出す瞬間まで接続の生死を確かめ
    ないので、Supabase 側に切られた接続が「空き」として並び続ける。閲覧者の
    リクエストがその 1 本を引くと、検査の失敗と再接続を取得待ちの中で払う
    ことになり、5 秒の上限を何度も踏んで 16-18 秒かかった末に空振りする
    (pool_available=1 と表示されているのに読み出しが 18.0 秒)。

    pool.check() は死んだ接続を捨てて張り直す。これを背景で回しておけば、
    その代金をリクエストではなくアイドル時間が払う。
    """
    global _PG_POOL_CHECKER_STARTED
    if _PG_POOL_CHECKER_STARTED:
        return
    # 既定は無効。2026-08-24 に導入したが、tcp_user_timeout が無い状態では
    # pool.check() が死んだソケット上で戻らなくなり、プールの全接続を掴んだまま
    # 固まってレース詳細が全滅した。tcp_user_timeout を入れた今は理屈の上では
    # 安全だが、同じ失敗を無検証で繰り返さないため、既定は切っておく。
    # 有効にするなら BOATRACE_DB_POOL_CHECK=1 を明示すること。
    if os.getenv("BOATRACE_DB_POOL_CHECK", "0") != "1":
        return
    if not hasattr(pool, "check"):
        return

    interval = _pool_check_interval_seconds()

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                pool.check()
            except Exception:
                logger.warning("pool health check failed", exc_info=True)

    thread = threading.Thread(
        target=_loop, name="pg-pool-health-check", daemon=True
    )
    thread.start()
    _PG_POOL_CHECKER_STARTED = True


def _get_pg_pool(dsn: str):
    global _PG_POOL
    if _PG_POOL is not None:
        return _PG_POOL
    with _PG_POOL_LOCK:
        if _PG_POOL is None:
            from psycopg_pool import ConnectionPool

            trigger = os.getenv("BOATRACE_TASK_TRIGGER", "").strip().lower()
            # 上限は「その worker が瞬間的に使ってよい本数」。Supabase の
            # pooler は session mode でクライアント 15 本が上限なので、web と
            # cron でその 15 本を分け合う。本番の値は render.yaml が持つ
            # (BOATRACE_DB_POOL_SIZE)。ここはその予算と揃えた既定値。
            default_pool_size = "1" if trigger else "6"
            # Web は使う分を最初から温めておく。Render(シンガポール) から
            # Supabase(東京) への新規接続は往復 + TLS で実測 2.5 秒かかり、
            # min_size=1 では 2 本目以降を毎回張り直していた。接続の取得待ちが
            # 積み上がってレース詳細が「準備中」に落ちた実障害の対策
            # (2026-08-22: peak_concurrent=1 / failures=0 なのに max_wait 2571ms)。
            # min_size は「常に張りっぱなしにする本数」。ここを大きくすると
            # 全 worker の合計が Supabase 側 (Supavisor) のクライアント枠を
            # 食い潰し、先に温まった worker が枠を占有して、もう一方が 1 本も
            # 取れないまま固まる。2026-08-24 に 4 -> 8 へ上げたところ、まさに
            # これが起きた: pid 83 は pool_available=3 で正常、pid 82 は
            # pool_available=0 のまま復帰せず、リクエストの約半分が 10 秒待って
            # 「準備しています」に落ちた。worker 数 x min_size が枠に収まる値に
            # 戻す (2 worker x 4 = 8 + cron 各 1)。
            default_min_size = 0 if trigger else 3
            max_size = max(
                1,
                int(os.getenv("BOATRACE_DB_POOL_SIZE", default_pool_size)),
            )
            # 待ち行列に上限を置かない (0 = 無制限)。
            # 2026-08-24 実障害の最終原因。psycopg_pool の「待っている件数」は
            # 一度増えると減らないことがあり、スレッド 3 本しかない worker で
            # requests_waiting=6 (=上限) のまま張り付いた。上限に達した瞬間から
            # 以後すべての取得が TooManyRequests で即座に弾かれ、待てば直る類の
            # 詰まりではないので永久に復帰しない。実際、再起動直後から 10 ページ
            # 連続で仮ページに落ちた。
            # 上限を外しても待ち手は各自 5 秒でタイムアウトするので、行列が
            # 無限に伸びることはない。壊れたカウンタで自らを閉め出す方が害が大きい。
            default_max_waiting = "0"
            max_waiting = max(
                0,
                int(
                    os.getenv(
                        "BOATRACE_DB_POOL_MAX_WAITING", default_max_waiting
                    )
                ),
            )

            _PG_POOL = ConnectionPool(
                conninfo=dsn,
                # 接続の「確立」に制限時間を掛ける。これが無いと、Render から
                # Supabase への TCP/TLS が応答を返さないときに接続作成が無期限に
                # 刺さり、プールは永久に空のまま復帰しない。2026-08-24 の実障害は
                # まさにこれで、Web だけが数時間 DB を掴めず (cron は直接接続なので
                # connect_timeout があり無事だった)、レース詳細が「準備しています」の
                # ままになった。プールの timeout は「空きを待つ時間」であって
                # 「接続を張る時間」ではないので、別に指定する必要がある。
                kwargs={
                    "connect_timeout": _pg_connect_timeout_seconds(),
                    **_pg_socket_keepalive_kwargs(),
                },
                min_size=max(
                    0,
                    int(os.getenv("BOATRACE_DB_POOL_MIN_SIZE", str(default_min_size))),
                ),
                # Supavisor has a finite client budget shared by web and cron
                # processes. Reserve four Web connections while cron processes
                # open at most one on demand and return to zero when idle.
                max_size=max_size,
                max_waiting=max_waiting,
                timeout=max(1, int(os.getenv("BOATRACE_DB_POOL_TIMEOUT_SEC", "5"))),
                max_lifetime=900,
                # 遊休接続を長く抱えすぎると、Supabase 側 (Supavisor) が先に
                # 黙って切る。こちらは「空き 3 本」と思ったまま死んだ接続を並べ、
                # 取り出すたびに check が失敗して捨て直すので、1 回の取得に
                # 10 秒以上かかりレース詳細が「準備しています」に落ちる
                # (2026-08-24: pool_available=3 なのに読み出しが 16.7 秒)。
                # こちらから先に retire すれば、張り直しは min_size を満たす
                # ための背景処理として行われ、リクエストの待ち時間にならない。
                max_idle=180,
                configure=_configure_pg_connection,
                check=ConnectionPool.check_connection,
                open=True,
            )
            if not trigger:
                _start_pool_health_checker(_PG_POOL)
    return _PG_POOL


def _pg_socket_keepalive_kwargs() -> dict[str, int]:
    """死んだ接続を OS に検知させる設定。

    Supavisor や途中の NAT が黙って接続を落とすと、こちら側の socket は
    「生きているつもり」のまま応答を待ち続け、そのスレッドは永久に戻らない。
    statement_timeout はサーバに届いて初めて効くので、この状況では役に立たない。
    keepalive を入れておくと概ね 60 秒で切断として例外になり、プールが張り直せる。
    """
    return {
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
        # keepalive は「無通信の接続」の死を見つける。これとは別に、送信済みの
        # 問い合わせに応答が返らない場合を縛るのが tcp_user_timeout (ミリ秒)。
        # これが無いと、死んだソケット上の SELECT は OS が諦めるまで戻らず、
        # その接続を掴んだ処理ごと固まる。2026-08-24 の実障害では、遊休接続の
        # 生死を確かめる pool.check() がまさにこれで固まり、プールの全接続を
        # 掴んだまま空き 0 / 待ち 6 から復帰しなくなった。
        "tcp_user_timeout": 5000,
    }


def _pg_connect_timeout_seconds() -> int:
    """TCP/TLS 確立そのものに掛ける制限時間 (秒)。

    これが無いと接続の確立は無期限に待つ。プール側の timeout は「空き接続を
    待つ時間」であって「接続を張る時間」ではないので、両方必要になる。
    """
    try:
        return max(1, int(os.getenv("BOATRACE_DB_CONNECT_TIMEOUT_SEC", "5")))
    except (TypeError, ValueError):
        return 5


def _open_direct_pg_connection(dsn: str):
    import psycopg

    connect_timeout = _pg_connect_timeout_seconds()
    conn = psycopg.connect(
        dsn,
        autocommit=True,
        connect_timeout=connect_timeout,
        **_pg_socket_keepalive_kwargs(),
    )
    _configure_pg_connection(conn)
    return conn


class _PgConnection:
    """psycopg3 connection を sqlite3 風に薄くラップ。
    `execute(sql, params)` で `?` を `%s` に変換しつつ ON CONFLICT を補完。"""

    def __init__(self, dsn: str, direct: bool = False):
        trigger = os.getenv("BOATRACE_TASK_TRIGGER", "").strip().lower()
        self._pool = None
        self._pool_acquired_at = None
        if trigger or direct:
            self._conn = _acquire_pg_connection(dsn, direct=True)
        else:
            self._pool = _get_pg_pool(dsn)
            checkout_started = time.monotonic()
            try:
                self._conn = _acquire_pg_connection(
                    dsn,
                    direct=False,
                    pool=self._pool,
                )
            except Exception as exc:
                _note_pool_checkout_failed(
                    self._pool,
                    (time.monotonic() - checkout_started) * 1000.0,
                    exc,
                )
                stats = _safe_pool_stats(self._pool)
                # ERROR handlers persist to incident_log through this same DB.
                # During pool exhaustion that would recursively attempt another
                # checkout and multiply one bounded wait into a long outage.
                # The Web layer buffers the transient error and lifecycle stats
                # for the next successful checkout, so keep this local log below
                # the synchronous error-notifier threshold.
                logger.warning("postgres pool checkout failed stats=%s", stats)
                _maybe_rebuild_exhausted_pg_pool(self._pool, stats)
                raise
            acquired_at = time.monotonic()
            self._pool_acquired_at = acquired_at
            try:
                _note_pool_acquired(
                    self._pool, (acquired_at - checkout_started) * 1000.0
                )
            except Exception:
                logger.warning("pool acquire bookkeeping failed", exc_info=True)
        # ここから先で例外が出ると、貸し出された接続は誰にも渡らないまま
        # 参照を失い、プールには二度と戻らない (psycopg_pool は GC では回収
        # しない)。返却されない接続が 1 本ずつ積み上がると、その worker は
        # やがて pool_available=0 のまま復帰しなくなる。2026-08-24 の実障害では
        # pid 82 が pool_size=9 / available=0 で固まり、リクエストの約半分が
        # 10 秒待って仮ページに落ちた。必ず返してから送出する。
        try:
            self._conn.autocommit = True
            self._kind = "postgres"
        except Exception:
            self.close()
            raise

    def execute(self, sql: str, params: Optional[tuple] = None):
        _count_sql()
        sql2 = _placeholder_pg(
            _rewrite_sqlite_specific(sql),
            escape_percent=params is not None,
        )
        cur = self._conn.cursor()
        if params is None:
            cur.execute(sql2)
        else:
            cur.execute(sql2, params)
        return cur

    def executemany(self, sql: str, seq):
        _count_sql()
        sql2 = _placeholder_pg(_rewrite_sqlite_specific(sql), escape_percent=True)
        cur = self._conn.cursor()
        cur.executemany(sql2, list(seq))
        return cur

    def executescript(self, script: str):
        _count_sql()
        # psycopg3 は単一 execute() で複文を受け付けないため、文ごとに分割して実行
        # まず行コメント (--) を除去してから ; で分割し、各文に書き換えを適用
        cleaned = "\n".join(
            line for line in script.splitlines()
            if not line.lstrip().startswith("--")
        )
        cur = self._conn.cursor()
        for stmt in cleaned.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(_rewrite_sqlite_specific(stmt))
        return cur

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        # autocommit なので no-op
        pass

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._conn is not None:
            if self._pool is None:
                self._conn.close()
            else:
                self._pool.putconn(self._conn)
                if self._pool_acquired_at is not None:
                    _note_pool_released(
                        self._pool,
                        (time.monotonic() - self._pool_acquired_at) * 1000.0,
                    )
                    self._pool_acquired_at = None
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    def __del__(self):
        """返し忘れの最後の砦。

        psycopg_pool は貸し出した接続を GC では回収しない。どこか 1 箇所でも
        close() を通らない経路があると、その 1 本は永久に失われ、積み上がると
        worker が pool_available=0 のまま復帰しなくなる (2026-08-24 実障害)。
        経路を数え上げて塞ぐのは大事だが、見落としが必ず 1 つ残る前提で、
        参照が消えた時点で必ずプールへ返す。発動回数は pg_pool_report() に
        出るので、「まだ塞げていない経路がある」ことに気づける。
        """
        global _PG_POOL_RECLAIMED_BY_GC
        try:
            if getattr(self, "_conn", None) is None:
                return
            if getattr(self, "_pool", None) is None:
                return
            _PG_POOL_RECLAIMED_BY_GC += 1
            self.close()
        except Exception:
            pass


class _MeasuredConnection:
    """Count SQLite API statements using the same round-trip semantics as PG."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._kind = "sqlite"

    def execute(self, sql: str, params=None):
        _count_sql()
        return self._conn.execute(sql) if params is None else self._conn.execute(sql, params)

    def executemany(self, sql: str, seq):
        _count_sql()
        return self._conn.executemany(sql, seq)

    def executescript(self, script: str):
        _count_sql()
        return self._conn.executescript(script)

    def cursor(self):
        return self._conn.cursor()

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def connect(
    db_path: Optional[str] = None,
    direct: bool = False,
) -> Union[sqlite3.Connection, "_PgConnection"]:
    """
    プロジェクト共通の DB 接続を返す。

    SQLite (デフォルト):
      - journal_mode=WAL: 読み書き同時を許可
      - busy_timeout: 他プロセスのロック解放まで待機
      - foreign_keys=ON: FK 制約を有効化

    PostgreSQL (DATABASE_URL 設定時):
      - psycopg3 で接続
      - autocommit=True
      - SQLite 構文を最低限書き換えて execute

    direct=True (Postgres のみ効果):
      - 共有プールを経由せず短命の直結接続を開く。
      - ログイン/会員確認などの認証クリティカル経路が、重いページ処理による
        プール枯渇 (PoolTimeout) に巻き込まれないようにするためのもの。
      - SQLite では通常接続と同じ動作。
    """
    # 明示的に db_path が渡された場合は、そのローカル SQLite を最優先する。
    # バックフィル/検証スクリプトでは .env の DATABASE_URL が残っていても、
    # 指定した DB ファイルに対して確実に処理したい。
    if db_path:
        path = db_path
        conn = sqlite3.connect(path, timeout=config.SQLITE_CONNECT_TIMEOUT_SECONDS)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS};")
        conn.execute("PRAGMA foreign_keys=ON;")
        return _MeasuredConnection(conn) if os.getenv("BOATRACE_MEASURE_SQL") else conn

    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url and _is_postgres_url(db_url):
        return _PgConnection(_normalize_pg_url(db_url), direct=direct)

    # 本番 (Render) で DATABASE_URL 空はサイレント SQLite フォールバックで
    # 壊滅的バグになる (空 DB で起動する)。明示的に失敗させる。
    if os.getenv("RENDER", "").strip():
        raise RuntimeError(
            "DATABASE_URL is empty in RENDER environment. "
            "Set DATABASE_URL to the Supabase Postgres URL. "
            "Refusing to silently fall back to SQLite in production."
        )

    # SQLite path (ローカル開発時のみ)
    path = config.DB_PATH
    conn = sqlite3.connect(path, timeout=config.SQLITE_CONNECT_TIMEOUT_SECONDS)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS};")
    conn.execute("PRAGMA foreign_keys=ON;")
    return _MeasuredConnection(conn) if os.getenv("BOATRACE_MEASURE_SQL") else conn
