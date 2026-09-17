"""本番サイトの日別アクセス数を永続化する台帳。

Cloudflare Web Analytics のデータ保持は 30 日だけで、それを過ぎると取り返せない。
毎日ここに写しておくことで、30 日より前の推移も残す。

設計上の要点:
  * Cloudflare は「表示ゼロの日」を返さない。返ってこなかった日を素朴に無視すると
    グラフに穴が空き、0 で埋めると保持期間外の実績を 0 で潰してしまう。
    そこで `record_daily_access` は **保持期間内の欠落日だけ** 0 として記録し、
    保持期間より前の行には触らない。
  * 数値は後から確定することがあるため upsert（同じ日は上書き）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Sequence

SOURCE_CLOUDFLARE = "cloudflare"
# アプリ自身が数えた分。Cloudflare のビーコンは Render ドメインだと CORS で
# 弾かれて取りこぼすので (2026-09-09 確認)、サーバー側でも別立てで数える。
# 同じ日を両方が持つため、必ず source で分けて保存する。
SOURCE_SERVER = "server"
_SCHEMA_READY = False


def ensure_site_access_table(conn: Any) -> None:
    """日別アクセス台帳を SQLite / PostgreSQL の両方で作る。"""
    global _SCHEMA_READY
    is_postgres = getattr(conn, "_kind", "") == "postgres"
    if is_postgres and _SCHEMA_READY:
        return
    if is_postgres:
        try:
            conn.execute("SELECT 1 FROM site_access_daily LIMIT 0")
            _SCHEMA_READY = True
            return
        except Exception:
            pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS site_access_daily (
            access_date TEXT NOT NULL,
            source TEXT NOT NULL,
            page_views INTEGER NOT NULL DEFAULT 0,
            visits INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (access_date, source)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_site_access_daily_date "
        "ON site_access_daily(access_date)"
    )
    if is_postgres:
        _SCHEMA_READY = True


def record_daily_access(
    conn: Any,
    rows: Iterable[dict],
    *,
    source: str = SOURCE_CLOUDFLARE,
    window_start: str | None = None,
    window_end: str | None = None,
) -> dict[str, int]:
    """取得した日別アクセス数を台帳に反映する。

    rows: [{"date": "YYYY-MM-DD", "page_views": int, "visits": int}, ...]
          計測元が返した日だけを渡す（ゼロの日は含まなくてよい）。
    window_start / window_end: 計測元がカバーしていた期間。
          この範囲にありながら rows に無い日は「表示ゼロの日」として 0 で記録する。
          範囲外（＝保持期間を過ぎて取得できない日）の既存行には触らない。
    """
    ensure_site_access_table(conn)
    stamp = datetime.now().isoformat(timespec="seconds")

    payload: dict[str, tuple[int, int]] = {}
    for row in rows:
        date = str(row.get("date") or "").strip()
        if not date:
            continue
        payload[date] = (int(row.get("page_views") or 0), int(row.get("visits") or 0))

    zero_filled = 0
    if window_start and window_end:
        for date in _date_range(window_start, window_end):
            if date not in payload:
                payload[date] = (0, 0)
                zero_filled += 1

    for date, (views, visits) in sorted(payload.items()):
        conn.execute(
            """
            INSERT INTO site_access_daily
                (access_date, source, page_views, visits, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (access_date, source) DO UPDATE SET
                page_views = EXCLUDED.page_views,
                visits = EXCLUDED.visits,
                updated_at = EXCLUDED.updated_at
            """,
            (date, source, views, visits, stamp),
        )

    return {
        "written": len(payload),
        "from_source": len(payload) - zero_filled,
        "zero_filled": zero_filled,
    }


def add_page_views(
    conn: Any,
    counts: dict[str, int],
    *,
    source: str = SOURCE_SERVER,
) -> int:
    """日ごとのページ表示数を台帳に **足しこむ**。

    `record_daily_access` が上書きなのに対し、こちらは加算。
    本番は gunicorn の複数ワーカーが別プロセスで動いており、それぞれが
    自分の数えた分を持ち寄るため、上書きにすると片方の数が消える。

    counts: {"YYYY-MM-DD": 件数} 。0 以下の日は無視する。
    戻り値: 実際に書いた日数。
    """
    ensure_site_access_table(conn)
    stamp = datetime.now().isoformat(timespec="seconds")
    written = 0
    for date, count in sorted(counts.items()):
        if not date or int(count) <= 0:
            continue
        conn.execute(
            """
            INSERT INTO site_access_daily
                (access_date, source, page_views, visits, updated_at)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT (access_date, source) DO UPDATE SET
                page_views = site_access_daily.page_views + EXCLUDED.page_views,
                updated_at = EXCLUDED.updated_at
            """,
            (date, source, int(count), stamp),
        )
        written += 1
    return written


def load_daily_access(
    conn: Any,
    *,
    start: str,
    end: str,
    source: str = SOURCE_CLOUDFLARE,
) -> list[dict]:
    """台帳から期間内の日別アクセス数を返す。記録が無い日は 0 で埋めて連続させる。"""
    ensure_site_access_table(conn)
    cur = conn.execute(
        """
        SELECT access_date, page_views, visits
        FROM site_access_daily
        WHERE source = ? AND access_date >= ? AND access_date <= ?
        ORDER BY access_date
        """,
        (source, start, end),
    )
    stored = {str(r[0]): (int(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()}
    return [
        {
            "date": date,
            "views": stored.get(date, (0, 0))[0],
            "visits": stored.get(date, (0, 0))[1],
            "recorded": date in stored,
        }
        for date in _date_range(start, end)
    ]


def access_coverage(conn: Any, *, source: str = SOURCE_CLOUDFLARE) -> dict:
    """台帳が何日分たまっているかを返す（監視・報告用）。"""
    ensure_site_access_table(conn)
    cur = conn.execute(
        """
        SELECT MIN(access_date), MAX(access_date), COUNT(*), COALESCE(SUM(page_views), 0)
        FROM site_access_daily WHERE source = ?
        """,
        (source,),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return {"first_date": None, "last_date": None, "days": 0, "total_views": 0}
    return {"first_date": row[0], "last_date": row[1], "days": int(row[2]), "total_views": int(row[3])}


def _date_range(start: str, end: str) -> Sequence[str]:
    from datetime import date as _date, timedelta

    first = _date.fromisoformat(start)
    last = _date.fromisoformat(end)
    if last < first:
        return []
    return [str(first + timedelta(days=i)) for i in range((last - first).days + 1)]
