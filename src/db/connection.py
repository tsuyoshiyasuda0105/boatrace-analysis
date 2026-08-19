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

_DEFAULT_CONNECT_RETRY_DELAYS = (0.2, 0.5)
_MAX_CONNECT_RETRIES = 2
_MAX_CONNECT_RETRY_DELAY_SEC = 0.5
_DEFAULT_POOL_EXHAUSTION_SEC = 90.0
_DEFAULT_POOL_REBUILD_COOLDOWN_SEC = 60.0


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
        try:
            conn = _open_direct_pg_connection(dsn) if direct else pool.getconn()
            if not direct:
                _note_pg_pool_checkout_success(pool)
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


def _build_upsert(table: str, columns: list[str], kind: str) -> str:
    """ON CONFLICT (pk) DO UPDATE SET col=EXCLUDED.col の SQL 末尾を生成。"""
    kind = kind.upper()
    if kind == "IGNORE":
        return " ON CONFLICT DO NOTHING"
    if kind != "REPLACE":
        raise ValueError(f"Unsupported SQLite INSERT OR kind: {kind}")
    pk = _TABLE_PRIMARY_KEYS.get(table.lower())
    if not pk:
        # REPLACE must never silently degrade to DO NOTHING.
        raise ValueError(
            f"INSERT OR REPLACE target table '{table}' is missing from "
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
    r"\bINSERT\s+OR\s+(REPLACE|IGNORE)\s+INTO\s+(\w+)\s*\(([^)]+)\)",
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


def _get_pg_pool(dsn: str):
    global _PG_POOL
    if _PG_POOL is not None:
        return _PG_POOL
    with _PG_POOL_LOCK:
        if _PG_POOL is None:
            from psycopg_pool import ConnectionPool

            trigger = os.getenv("BOATRACE_TASK_TRIGGER", "").strip().lower()
            default_pool_size = "1" if trigger else "4"
            default_min_size = 0 if trigger else 1
            max_size = max(
                1,
                int(os.getenv("BOATRACE_DB_POOL_SIZE", default_pool_size)),
            )
            default_max_waiting = "0" if trigger else str(max_size)
            max_waiting = max(
                0 if trigger else 1,
                int(
                    os.getenv(
                        "BOATRACE_DB_POOL_MAX_WAITING", default_max_waiting
                    )
                ),
            )

            _PG_POOL = ConnectionPool(
                conninfo=dsn,
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
                max_idle=120,
                configure=_configure_pg_connection,
                check=ConnectionPool.check_connection,
                open=True,
            )
    return _PG_POOL


def _open_direct_pg_connection(dsn: str):
    import psycopg

    connect_timeout = max(
        1,
        int(os.getenv("BOATRACE_DB_CONNECT_TIMEOUT_SEC", "5")),
    )
    conn = psycopg.connect(
        dsn,
        autocommit=True,
        connect_timeout=connect_timeout,
    )
    _configure_pg_connection(conn)
    return conn


class _PgConnection:
    """psycopg3 connection を sqlite3 風に薄くラップ。
    `execute(sql, params)` で `?` を `%s` に変換しつつ ON CONFLICT を補完。"""

    def __init__(self, dsn: str, direct: bool = False):
        trigger = os.getenv("BOATRACE_TASK_TRIGGER", "").strip().lower()
        self._pool = None
        if trigger or direct:
            self._conn = _acquire_pg_connection(dsn, direct=True)
        else:
            self._pool = _get_pg_pool(dsn)
            try:
                self._conn = _acquire_pg_connection(
                    dsn,
                    direct=False,
                    pool=self._pool,
                )
            except Exception:
                stats = {}
                try:
                    stats = self._pool.get_stats()
                except Exception:
                    pass
                logger.error("postgres pool checkout failed stats=%s", stats)
                _maybe_rebuild_exhausted_pg_pool(self._pool, stats)
                raise
        self._conn.autocommit = True
        self._kind = "postgres"

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
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


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
