"""Fetch and compare external accident-rate rankings against internal stats.

The InterQ class page is currently the closest practical public benchmark for
the official period accident ranking. Render/Supabase can rebuild internal
accident stats only from the raw files available in that environment, so this
checker exists to catch silent drift when historical K-result files are
missing or parsing regresses.

Usage:
  python scripts/check_external_accident_snapshot.py
  python scripts/check_external_accident_snapshot.py --date 2026-08-05
  python scripts/check_external_accident_snapshot.py --no-write-status
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config  # noqa: E402
from src.db.connection import assert_safe_production_write, connect as db_connect  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

SOURCE_HTML_URL = "http://www.interq.or.jp/ito/kiida/kyotei/Class/class2000.html"
SOURCE_JS_BASE = "http://www.interq.or.jp/ito/kiida/kyotei/ajs/"
JS_FILES = {
    "plain": "plain2000.js",
    "tensu": "tensu2000.js",
}
CHECK_NAME = "accident_external_compare"
RECONSTRUCTION_CHECK_NAME = "accident_reconstruction_gap"
RULE_VERSION = "official_table_2025_05_reconstructed_v2"
ROW_RE = re.compile(r"yp\[(\d+)\]='([^']*)';")
XP_RE = re.compile(r"xp\[(\d+)\]='([^']*)';")
PERIOD_RE = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")
YCUR_RE = re.compile(r"var\s+YcurY=(\d{4}),\s*YcurM=(\d{1,2}),\s*YcurD=(\d{1,2});")


@dataclass(frozen=True)
class ExternalAccidentRow:
    racer_number: int
    starts_count: int
    accident_points: int
    accident_rate: float | None
    accident_codes_raw: str
    raw_profile: str


def fetch_text(url: str, *, encoding: str = "shift_jis", timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": config.USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 - explicit external benchmark source
        raw = resp.read()
    return raw.decode(encoding, errors="ignore")


def parse_period(html: str, tensu_js: str) -> tuple[str, str]:
    start_match = PERIOD_RE.search(html)
    end_match = YCUR_RE.search(tensu_js)
    if not start_match or not end_match:
        raise RuntimeError("failed to parse external accident period from HTML/JS")
    y1, m1, d1 = [int(x) for x in start_match.groups()]
    y2, m2, d2 = [int(x) for x in end_match.groups()]
    return date(y1, m1, d1).isoformat(), date(y2, m2, d2).isoformat()


def parse_js_rows(tensu_js: str, plain_js: str) -> dict[int, ExternalAccidentRow]:
    yp = {int(k): v for k, v in ROW_RE.findall(tensu_js)}
    xp = {int(k): v for k, v in XP_RE.findall(plain_js)}

    def starts_count(raw: str) -> int:
        return int(raw[21:23], 16)

    def accident_codes(raw: str) -> str:
        return raw[37:]

    def accident_points(raw: str) -> int:
        total = 0
        for bit in accident_codes(raw):
            if bit in "FLUWX":
                total += 20
            elif bit == "x":
                total += 15
            elif bit in "skubcde":
                total += 10
            elif bit in "tr":
                total += 2
        return total

    rows: dict[int, ExternalAccidentRow] = {}
    for racer_number, raw in yp.items():
        starts = starts_count(raw)
        points = accident_points(raw)
        rate = round(points / starts, 2) if starts > 0 else None
        rows[racer_number] = ExternalAccidentRow(
            racer_number=racer_number,
            starts_count=starts,
            accident_points=points,
            accident_rate=rate,
            accident_codes_raw=accident_codes(raw),
            raw_profile=xp.get(racer_number, ""),
        )
    return rows


def ensure_external_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS racer_accident_external_snapshots (
          snapshot_date       TEXT NOT NULL,
          racer_number        INTEGER NOT NULL,
          period_start        TEXT,
          period_end          TEXT,
          starts_count        INTEGER,
          accident_points     INTEGER,
          accident_rate       REAL,
          accident_codes_raw  TEXT,
          source_url          TEXT,
          source_kind         TEXT NOT NULL DEFAULT 'external',
          raw_payload         TEXT,
          created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (snapshot_date, racer_number, source_kind)
        )
        """
    )
    conn.commit()


def upsert_status(conn, check_name: str, check_date: str, status: str, message: str, detail: dict[str, Any]) -> None:
    now_iso = datetime.now().isoformat(timespec="seconds")
    detail_json = json.dumps(detail, ensure_ascii=False)
    row = conn.execute(
        "SELECT 1 FROM system_status WHERE check_name=? AND check_date=?",
        (check_name, check_date),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE system_status SET status=?, message=?, detail_json=?, checked_at=? "
            "WHERE check_name=? AND check_date=?",
            (status, message, detail_json, now_iso, check_name, check_date),
        )
    else:
        conn.execute(
            "INSERT INTO system_status (check_name, check_date, status, message, detail_json, checked_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (check_name, check_date, status, message, detail_json, now_iso),
        )
    conn.commit()


def save_external_snapshot(
    conn,
    *,
    snapshot_date: str,
    period_start: str,
    period_end: str,
    rows: dict[int, ExternalAccidentRow],
) -> None:
    ensure_external_table(conn)
    conn.executemany(
        """
        INSERT OR REPLACE INTO racer_accident_external_snapshots
          (snapshot_date, racer_number, period_start, period_end, starts_count,
           accident_points, accident_rate, accident_codes_raw, source_url,
           source_kind, raw_payload)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'interq_class2000', ?)
        """,
        [
            (
                snapshot_date,
                racer_number,
                period_start,
                period_end,
                row.starts_count,
                row.accident_points,
                row.accident_rate,
                row.accident_codes_raw,
                SOURCE_HTML_URL,
                row.raw_profile,
            )
            for racer_number, row in rows.items()
        ],
    )
    conn.commit()


def _period_key(period_start: str) -> tuple[int, int]:
    y, m, _d = [int(x) for x in str(period_start).split("-")]
    if 5 <= m <= 10:
        return y + 1, 1
    return y + 1, 2


def mirror_external_period_stats(
    conn,
    *,
    period_start: str,
    period_end: str,
    rows: dict[int, ExternalAccidentRow],
) -> None:
    period_year, period_half = _period_key(period_start)
    conn.execute(
        """
        DELETE FROM racer_accident_period_stats
         WHERE period_start = ?
           AND period_end = ?
           AND source_kind = 'official_external'
           AND rule_version = ?
        """,
        (period_start, period_end, RULE_VERSION),
    )
    payload = [
        (
            int(racer_number),
            period_year,
            period_half,
            period_start,
            period_end,
            int(row.starts_count or 0),
            len(str(row.accident_codes_raw or "")),
            int(row.accident_points or 0),
            float(row.accident_rate) if row.accident_rate is not None else None,
            RULE_VERSION,
            "official_external",
        )
        for racer_number, row in rows.items()
        if row.starts_count > 0 or row.accident_points > 0
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO racer_accident_period_stats
          (racer_number, period_year, period_half, period_start, period_end,
           starts_count, accident_events, accident_points, accident_rate,
           rule_version, source_kind, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        payload,
    )
    conn.commit()


def load_period_rows(
    conn,
    period_start: str,
    *,
    source_kind: str,
    period_end: str | None = None,
) -> dict[int, dict[str, Any]]:
    where = ["period_start = ?", "source_kind = ?", "rule_version = ?"]
    params: list[Any] = [period_start, source_kind, RULE_VERSION]
    if period_end:
        where.append("period_end = ?")
        params.append(period_end)
    cur = conn.execute(
        f"""
        SELECT racer_number, starts_count, accident_points, accident_rate, period_end
          FROM racer_accident_period_stats
         WHERE {" AND ".join(where)}
        """,
        tuple(params),
    )
    rows: dict[int, dict[str, Any]] = {}
    for racer_number, starts_count, accident_points, accident_rate, period_end in cur.fetchall():
        rows[int(racer_number)] = {
            "starts_count": int(starts_count or 0),
            "accident_points": int(accident_points or 0),
            "accident_rate": round(float(accident_rate or 0.0), 2),
            "period_end": str(period_end) if period_end else None,
        }
    return rows


def load_internal_rows(conn, period_start: str) -> dict[int, dict[str, Any]]:
    return load_period_rows(conn, period_start, source_kind="reconstructed")


def compare_rows(
    external_rows: dict[int, ExternalAccidentRow],
    internal_rows: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    mismatches: list[dict[str, Any]] = []
    compared_rows = 0
    compared_with_points = 0
    missing_internal = 0

    for racer_number, ext in external_rows.items():
        if ext.starts_count <= 0 and ext.accident_points <= 0:
            continue
        compared_rows += 1
        if ext.accident_points > 0:
            compared_with_points += 1
        internal = internal_rows.get(racer_number)
        if internal is None:
            missing_internal += 1
            mismatches.append(
                {
                    "racer_number": racer_number,
                    "external": {
                        "starts_count": ext.starts_count,
                        "accident_points": ext.accident_points,
                        "accident_rate": ext.accident_rate,
                        "codes": ext.accident_codes_raw,
                    },
                    "internal": None,
                }
            )
            continue
        same = (
            ext.starts_count == internal["starts_count"]
            and ext.accident_points == internal["accident_points"]
            and round(float(ext.accident_rate or 0.0), 2) == round(float(internal["accident_rate"] or 0.0), 2)
        )
        if same:
            continue
        mismatches.append(
            {
                "racer_number": racer_number,
                "external": {
                    "starts_count": ext.starts_count,
                    "accident_points": ext.accident_points,
                    "accident_rate": ext.accident_rate,
                    "codes": ext.accident_codes_raw,
                },
                "internal": internal,
            }
        )

    point_mismatch_rows = sum(
        1
        for row in mismatches
        if (row["external"]["accident_points"] or 0) != int((row.get("internal") or {}).get("accident_points") or 0)
    )
    top_mismatches = sorted(
        mismatches,
        key=lambda row: (
            -(row["external"]["accident_points"] or 0),
            -(row["external"]["starts_count"] or 0),
            row["racer_number"],
        ),
    )[:25]
    mismatch_ratio = (len(mismatches) / compared_rows) if compared_rows else 1.0
    nonzero_point_coverage = 0.0
    if compared_with_points:
        matching_with_points = compared_with_points - sum(
            1 for row in mismatches if (row["external"]["accident_points"] or 0) > 0
        )
        nonzero_point_coverage = matching_with_points / compared_with_points
    return {
        "compared_rows": compared_rows,
        "compared_with_points": compared_with_points,
        "mismatch_rows": len(mismatches),
        "point_mismatch_rows": point_mismatch_rows,
        "missing_internal_rows": missing_internal,
        "mismatch_ratio": round(mismatch_ratio, 4),
        "nonzero_point_coverage": round(nonzero_point_coverage, 4),
        "top_mismatches": top_mismatches,
    }


def status_from_summary(summary: dict[str, Any]) -> tuple[str, str]:
    compared_rows = int(summary.get("compared_rows") or 0)
    mismatch_rows = int(summary.get("mismatch_rows") or 0)
    point_mismatch_rows = int(summary.get("point_mismatch_rows") or 0)
    coverage = float(summary.get("nonzero_point_coverage") or 0.0)

    if compared_rows == 0:
        return "error", "外部事故率との照合対象が0件です"
    if point_mismatch_rows > 0 or coverage < 0.98:
        return (
            "warning",
            f"事故率監査差分あり: points差分={point_mismatch_rows}件 / 非ゼロ事故点一致率={coverage:.1%}",
        )
    if mismatch_rows > 0:
        return "warning", f"事故率照合に差分あり: {mismatch_rows}件"
    return "ok", f"事故率照合OK: {compared_rows}件一致"


def build_and_compare(check_date: str) -> dict[str, Any]:
    html = fetch_text(SOURCE_HTML_URL)
    plain_js = fetch_text(SOURCE_JS_BASE + JS_FILES["plain"])
    tensu_js = fetch_text(SOURCE_JS_BASE + JS_FILES["tensu"])
    period_start, period_end = parse_period(html, tensu_js)
    external_rows = parse_js_rows(tensu_js, plain_js)

    with db_connect() as conn:
        save_external_snapshot(
            conn,
            snapshot_date=check_date,
            period_start=period_start,
            period_end=period_end,
            rows=external_rows,
        )
        mirror_external_period_stats(
            conn,
            period_start=period_start,
            period_end=period_end,
            rows=external_rows,
        )
        official_rows = load_period_rows(
            conn,
            period_start,
            source_kind="official_external",
            period_end=period_end,
        )
        reconstruction_summary = compare_rows(external_rows, load_internal_rows(conn, period_start))
        summary = compare_rows(external_rows, official_rows)

    summary.update(
        {
            "check_date": check_date,
            "period_start": period_start,
            "period_end": period_end,
            "external_rows": len(external_rows),
            "internal_rows": len(official_rows),
            "internal_source_kind": "official_external",
            "reconstruction_audit": reconstruction_summary,
            "source_url": SOURCE_HTML_URL,
        }
    )
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(), help="check_date written into snapshots/system_status")
    ap.add_argument("--no-write-status", action="store_true")
    args = ap.parse_args()

    assert_safe_production_write(
        action="check_external_accident_snapshot",
        allow_env_var="BOATRACE_ALLOW_ACCIDENT_PROD_WRITE",
    )
    summary = build_and_compare(args.date)
    status, message = status_from_summary(summary)
    print(json.dumps({"status": status, "message": message, **summary}, ensure_ascii=False, indent=2))

    if not args.no_write_status:
        with db_connect() as conn:
            upsert_status(conn, CHECK_NAME, args.date, status, message, summary)
            reconstruction_summary = summary.get("reconstruction_audit") or {}
            reconstruction_status, reconstruction_message = status_from_summary(reconstruction_summary)
            upsert_status(
                conn,
                RECONSTRUCTION_CHECK_NAME,
                args.date,
                reconstruction_status,
                reconstruction_message,
                {
                    **reconstruction_summary,
                    "check_date": args.date,
                    "period_start": summary.get("period_start"),
                    "period_end": summary.get("period_end"),
                    "source_url": summary.get("source_url"),
                    "note": "Reference audit only. Production accident rates use official_external.",
                },
            )
    return 0 if status in {"ok", "warning"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
