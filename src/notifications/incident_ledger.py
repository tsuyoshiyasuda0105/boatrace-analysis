"""Best-effort shared incident ledger backed by ``incident_log``.

The ledger is intentionally independent from email delivery.  Every public
function absorbs database failures so observability can never stop the caller's
main flow.  Set ``BOATRACE_INCIDENT_APP_NAME`` per application to share one
Supabase table while keeping application views separate.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.db.connection import connect as db_connect


logger = logging.getLogger(__name__)
JST = ZoneInfo("Asia/Tokyo")
DEFAULT_APP_NAME = "boatrace"
_ACTIVE_STATUSES = ("open", "investigating")
_TERMINAL_STATUSES = ("resolved", "wontfix")
_LIST_COLUMNS = (
    "incident_id",
    "app_name",
    "occurred_at",
    "category",
    "source",
    "title",
    "detail",
    "severity",
    "dedup_key",
    "occurrence_count",
    "last_seen_at",
    "notified",
    "status",
    "resolved_at",
    "handled_by",
    "response_note",
    "updated_at",
)


def _now_jst() -> datetime:
    return datetime.now(JST)


def _app_name(value: str | None = None) -> str:
    return (value or os.getenv("BOATRACE_INCIDENT_APP_NAME") or DEFAULT_APP_NAME).strip() or DEFAULT_APP_NAME


def normalize_incident_key(source: str, title: str, *, error_type: str | None = None) -> str:
    """Build a stable family key after removing changing stats and numbers."""
    message = re.sub(r"\s+stats\s*=\s*\{.*$", "", str(title), flags=re.IGNORECASE)
    message = re.sub(r"\b\d+(?:\.\d+)?\b", "<n>", message)
    message = re.sub(r"\s+", " ", message).strip().lower()
    parts = [str(source).strip().lower() or "unknown"]
    if error_type:
        parts.append(str(error_type).strip().lower())
    parts.append(message[:160] or "untitled")
    return "|".join(parts)


def _serialize_detail(detail: Any) -> str | None:
    if detail is None:
        return None
    if isinstance(detail, str):
        return detail
    return json.dumps(detail, ensure_ascii=False, sort_keys=True, default=str)


def _incident_id(app_name: str, dedup_key: str, now: datetime) -> str:
    utc = now.astimezone(timezone.utc)
    stamp = utc.strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(f"{app_name}|{dedup_key}|{stamp}".encode("utf-8")).hexdigest()[:8]
    safe_app = re.sub(r"[^a-zA-Z0-9_-]+", "-", app_name).strip("-") or "app"
    return f"{safe_app}-{stamp}-{digest}"


def _rowcount(cursor: Any) -> int:
    value = getattr(cursor, "rowcount", 0)
    return int(value) if isinstance(value, int) and value > 0 else 0


def record_incident(
    *,
    category: str,
    source: str,
    title: str,
    detail: Any = None,
    severity: str = "error",
    dedup_key: str | None = None,
    app_name: str | None = None,
    notified: bool = False,
    conn: Any = None,
) -> str | None:
    """Create or aggregate an active incident, returning its ID when possible."""
    owned_connection = conn is None
    connection = None
    try:
        selected_app = _app_name(app_name)
        selected_key = dedup_key or normalize_incident_key(source, title)
        now = _now_jst()
        now_iso = now.replace(tzinfo=None).isoformat(timespec="microseconds")
        detail_text = _serialize_detail(detail)
        connection = conn or db_connect()

        update_params = (
            now_iso,
            title,
            detail_text,
            severity,
            int(bool(notified)),
            now_iso,
            selected_app,
            selected_key,
        )
        cursor = connection.execute(
            """
            UPDATE incident_log
               SET occurrence_count=occurrence_count + 1,
                   last_seen_at=?, title=?, detail=?, severity=?,
                   notified=CASE WHEN notified=1 OR ?=1 THEN 1 ELSE 0 END,
                   updated_at=?
             WHERE incident_id=(
                   SELECT incident_id FROM incident_log
                    WHERE app_name=? AND dedup_key=?
                      AND status IN ('open', 'investigating')
                    ORDER BY occurred_at DESC LIMIT 1
             )
            """,
            update_params,
        )
        if not _rowcount(cursor):
            new_id = _incident_id(selected_app, selected_key, now)
            insert_cursor = connection.execute(
                """
                INSERT OR IGNORE INTO incident_log
                    (incident_id, app_name, occurred_at, category, source, title,
                     detail, severity, dedup_key, occurrence_count, last_seen_at,
                     notified, status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'open', ?)
                """,
                (
                    new_id,
                    selected_app,
                    now_iso,
                    category,
                    source,
                    title,
                    detail_text,
                    severity,
                    selected_key,
                    now_iso,
                    int(bool(notified)),
                    now_iso,
                ),
            )
            if not _rowcount(insert_cursor):
                # Another process inserted the same active family concurrently.
                connection.execute(
                    """
                    UPDATE incident_log
                       SET occurrence_count=occurrence_count + 1,
                           last_seen_at=?, title=?, detail=?, severity=?,
                           notified=CASE WHEN notified=1 OR ?=1 THEN 1 ELSE 0 END,
                           updated_at=?
                     WHERE incident_id=(
                           SELECT incident_id FROM incident_log
                            WHERE app_name=? AND dedup_key=?
                              AND status IN ('open', 'investigating')
                            ORDER BY occurred_at DESC LIMIT 1
                     )
                    """,
                    update_params,
                )
        connection.commit()
        row = connection.execute(
            """
            SELECT incident_id FROM incident_log
             WHERE app_name=? AND dedup_key=?
               AND status IN ('open', 'investigating')
             ORDER BY occurred_at DESC LIMIT 1
            """,
            (selected_app, selected_key),
        ).fetchone()
        return str(row[0]) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("incident ledger record failed: %s: %s", type(exc).__name__, exc)
        return None
    finally:
        if owned_connection and connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


def resolve_incident(
    incident_id_or_dedup_key: str,
    *,
    handled_by: str,
    response_note: str,
    status: str = "resolved",
) -> bool:
    """Update response history by exact ID or the current app's active dedup key."""
    connection = None
    try:
        if status not in {*_ACTIVE_STATUSES, *_TERMINAL_STATUSES}:
            raise ValueError(f"unsupported incident status: {status}")
        now_iso = _now_jst().replace(tzinfo=None).isoformat(timespec="microseconds")
        resolved_at = now_iso if status in _TERMINAL_STATUSES else None
        connection = db_connect()
        cursor = connection.execute(
            """
            UPDATE incident_log
               SET status=?, resolved_at=?, handled_by=?, response_note=?, updated_at=?
             WHERE incident_id=(
                   SELECT incident_id FROM incident_log
                    WHERE incident_id=?
                       OR (app_name=? AND dedup_key=?
                           AND status IN ('open', 'investigating'))
                    ORDER BY CASE WHEN incident_id=? THEN 0 ELSE 1 END,
                             occurred_at DESC
                    LIMIT 1
             )
            """,
            (
                status,
                resolved_at,
                handled_by,
                response_note,
                now_iso,
                incident_id_or_dedup_key,
                _app_name(),
                incident_id_or_dedup_key,
                incident_id_or_dedup_key,
            ),
        )
        connection.commit()
        return bool(_rowcount(cursor))
    except Exception as exc:  # noqa: BLE001
        logger.warning("incident ledger resolve failed: %s: %s", type(exc).__name__, exc)
        return False
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


def list_incidents(
    *,
    app_name: str | None = None,
    status: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return a bounded incident list, with active rows ordered first."""
    connection = None
    try:
        bounded_limit = max(1, min(int(limit), 200))
        clauses = ["app_name=?"]
        params: list[Any] = [_app_name(app_name)]
        if status:
            clauses.append("status=?")
            params.append(status)
        if since:
            clauses.append("last_seen_at>=?")
            params.append(since)
        params.append(bounded_limit)
        sql = f"""
            SELECT {', '.join(_LIST_COLUMNS)}
              FROM incident_log
             WHERE {' AND '.join(clauses)}
             ORDER BY CASE status
                        WHEN 'open' THEN 0 WHEN 'investigating' THEN 1 ELSE 2 END,
                      last_seen_at DESC
             LIMIT ?
        """
        connection = db_connect()
        rows = connection.execute(sql, tuple(params)).fetchall()
        return [dict(zip(_LIST_COLUMNS, row)) for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("incident ledger list failed: %s: %s", type(exc).__name__, exc)
        return []
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass
