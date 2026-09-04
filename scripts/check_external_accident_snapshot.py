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
  python scripts/check_external_accident_snapshot.py --dry-run
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
from src.deploy_info import log_deploy_revision  # noqa: E402
from src.notifications.cron_alerts import notify_cron_failure  # noqa: E402
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


def load_internal_rows(conn, period_start: str) -> dict[int, dict[str, Any]]:
    try:
        cur = conn.execute(
            """
            SELECT racer_number, starts_count, accident_events, accident_points, accident_rate, period_end
              FROM racer_accident_period_stats
             WHERE period_start = ?
               AND source_kind = 'reconstructed'
               AND rule_version = ?
               AND period_end = (
                   SELECT MAX(period_end)
                     FROM racer_accident_period_stats
                    WHERE period_start = ?
                      AND source_kind = 'reconstructed'
                      AND rule_version = ?
               )
            """,
            (period_start, RULE_VERSION, period_start, RULE_VERSION),
        )
        fetched_rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 - supports older local test schemas
        if "accident_events" not in str(exc):
            raise
        cur = conn.execute(
            """
            SELECT racer_number, starts_count, accident_points, accident_rate, period_end
              FROM racer_accident_period_stats
             WHERE period_start = ?
               AND source_kind = 'reconstructed'
               AND rule_version = ?
               AND period_end = (
                   SELECT MAX(period_end)
                     FROM racer_accident_period_stats
                    WHERE period_start = ?
                      AND source_kind = 'reconstructed'
                      AND rule_version = ?
               )
            """,
            (period_start, RULE_VERSION, period_start, RULE_VERSION),
        )
        fetched_rows = [(rn, starts, 0, points, rate, pend) for rn, starts, points, rate, pend in cur.fetchall()]
    internal: dict[int, dict[str, Any]] = {}
    for racer_number, starts_count, accident_events, accident_points, accident_rate, period_end in fetched_rows:
        internal[int(racer_number)] = {
            "starts_count": int(starts_count or 0),
            "accident_events": int(accident_events or 0),
            "accident_points": int(accident_points or 0),
            "accident_rate": round(float(accident_rate or 0.0), 2),
            "period_end": str(period_end) if period_end else None,
        }
    return internal


def calibrate_reconstructed_period_stats(
    conn,
    *,
    period_start: str,
    period_end: str,
    rows: dict[int, ExternalAccidentRow],
) -> None:
    """Align reconstructed period totals to the official external benchmark.

    The raw race-result reconstruction is intentionally kept as the event log.
    Period totals are the production source for accident rate tags, so we
    calibrate them to the official benchmark while recording the adjustment.
    """
    period_year, period_half = _period_key(period_start)
    existing = load_internal_rows(conn, period_start)
    conn.execute(
        """
        DELETE FROM racer_accident_period_adjustments
         WHERE period_start = ?
           AND period_end = ?
           AND rule_version = ?
           AND source_kind = 'interq_class2000_calibration'
        """,
        (period_start, period_end, RULE_VERSION),
    )
    adjustment_payload = []
    stats_payload = []
    for racer_number, row in rows.items():
        if row.starts_count <= 0 and row.accident_points <= 0:
            continue
        current = existing.get(racer_number, {})
        point_delta = int(row.accident_points or 0) - int(current.get("accident_points") or 0)
        event_delta = len(str(row.accident_codes_raw or "")) - int(current.get("accident_events") or 0)
        if point_delta or event_delta:
            adjustment_payload.append(
                (
                    int(racer_number),
                    period_start,
                    period_end,
                    RULE_VERSION,
                    int(point_delta),
                    int(event_delta),
                    "interq_class2000_calibration",
                    json.dumps(
                        {
                            "external_points": int(row.accident_points or 0),
                            "external_starts": int(row.starts_count or 0),
                            "external_codes": row.accident_codes_raw,
                            "previous_points": int(current.get("accident_points") or 0),
                            "previous_starts": int(current.get("starts_count") or 0),
                        },
                        ensure_ascii=False,
                    ),
                )
            )
        stats_payload.append(
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
                "reconstructed",
            )
        )
    if adjustment_payload:
        conn.executemany(
            """
            INSERT OR REPLACE INTO racer_accident_period_adjustments
              (racer_number, period_start, period_end, rule_version,
               adjustment_points, adjustment_events, source_kind, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            adjustment_payload,
        )
    conn.executemany(
        """
        INSERT OR REPLACE INTO racer_accident_period_stats
          (racer_number, period_year, period_half, period_start, period_end,
           starts_count, accident_events, accident_points, accident_rate,
           rule_version, source_kind, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        stats_payload,
    )
    conn.commit()


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


# 判定のしきい値は本番の実測から決めた (2026-08-05〜09-02 の pre_calibration)。
#   平常時 : 事故点差分 0〜48 件 = 0.0〜3.0% / 非ゼロ事故点一致率 0.49〜1.00
#   実障害 : 2026-08-05 の K ファイル欠損は 1029 件 = 63.5% / 一致率 0.001
# 平常のばらつきの倍以上、実障害のはるか手前に置く。出走数だけの差分は
# 取り込みが 1〜2 走遅れるだけで毎日 3 割前後出るため、判定には使わない。
POINT_MISMATCH_WARN_RATIO = 0.05
POINT_MISMATCH_ERROR_RATIO = 0.10
NONZERO_COVERAGE_WARN = 0.40
NONZERO_COVERAGE_ERROR = 0.25


def status_from_summary(summary: dict[str, Any]) -> tuple[str, str]:
    """照合結果を判定する。判定は必ず「補正前」の数字で行う。

    build_and_compare は比較のあとに外部の値を内部へ書き写して
    (mirror_external_period_stats / calibrate_reconstructed_period_stats)、
    その後でもう一度比較している。つまり補正後の summary は答えを写してから
    答え合わせをした数字で、ほぼ必ず一致する。実際 2026-08-24〜09-02 は
    補正前が毎日 3 割ズレていたのに 10 日連続で ok と記録されていた。
    このスクリプトの目的は K ファイル欠損やパース退行の検知なので、
    素の一致度だけが判定に使える数字になる。書き写し自体は「現行期間は
    外部を正とする」設計として残し、ここでは触らない。
    """
    audited = summary.get("pre_calibration") or summary
    compared_rows = int(audited.get("compared_rows") or 0)
    if compared_rows == 0:
        return "error", "外部事故率との照合対象が0件です"

    point_mismatch_rows = int(audited.get("point_mismatch_rows") or 0)
    coverage = float(audited.get("nonzero_point_coverage") or 0.0)
    starts_only_rows = max(0, int(audited.get("mismatch_rows") or 0) - point_mismatch_rows)
    point_ratio = point_mismatch_rows / compared_rows
    detail = (
        f"事故点差分={point_mismatch_rows}件({point_ratio:.1%}) / "
        f"非ゼロ事故点一致率={coverage:.1%} / 出走数のみ差分={starts_only_rows}件"
    )

    if point_ratio >= POINT_MISMATCH_ERROR_RATIO or coverage < NONZERO_COVERAGE_ERROR:
        return "error", f"事故率の再構築が外部と大きく乖離: {detail}"
    if point_ratio >= POINT_MISMATCH_WARN_RATIO or coverage < NONZERO_COVERAGE_WARN:
        return "warning", f"事故率照合に差分あり: {detail}"
    return "ok", f"事故率照合OK(補正前で判定): {detail}"


def fetch_external_data() -> tuple[str, str, dict[int, ExternalAccidentRow]]:
    """Fetch and parse the benchmark without touching the database."""
    html = fetch_text(SOURCE_HTML_URL)
    plain_js = fetch_text(SOURCE_JS_BASE + JS_FILES["plain"])
    tensu_js = fetch_text(SOURCE_JS_BASE + JS_FILES["tensu"])
    period_start, period_end = parse_period(html, tensu_js)
    external_rows = parse_js_rows(tensu_js, plain_js)
    if not external_rows:
        raise RuntimeError("external accident source parsed zero racer rows")
    return period_start, period_end, external_rows


def build_and_compare(check_date: str, *, dry_run: bool = False) -> dict[str, Any]:
    period_start, period_end, external_rows = fetch_external_data()

    with db_connect() as conn:
        internal_rows = load_internal_rows(conn, period_start)
        pre_calibration_summary = compare_rows(external_rows, internal_rows)
        if dry_run:
            summary = dict(pre_calibration_summary)
        else:
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
            calibrate_reconstructed_period_stats(
                conn,
                period_start=period_start,
                period_end=period_end,
                rows=external_rows,
            )
            internal_rows = load_internal_rows(conn, period_start)
            summary = compare_rows(external_rows, internal_rows)

    summary.update(
        {
            "check_date": check_date,
            "period_start": period_start,
            "period_end": period_end,
            "external_rows": len(external_rows),
            "internal_rows": len(internal_rows),
            "pre_calibration": pre_calibration_summary,
            "calibration_source_kind": (
                None if dry_run else "interq_class2000_calibration"
            ),
            "dry_run": dry_run,
            "writes_performed": not dry_run,
            "source_url": SOURCE_HTML_URL,
        }
    )
    return summary


CRON_JOB_NAME = "boatrace-accident-external-check-cron"


def _main_impl() -> int:
    log_deploy_revision(CRON_JOB_NAME)
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat(), help="check_date written into snapshots/system_status")
    ap.add_argument("--no-write-status", action="store_true")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch, parse, and compare without DB/schema/status writes",
    )
    args = ap.parse_args()

    if not args.dry_run:
        assert_safe_production_write(
            action="check_external_accident_snapshot",
            allow_env_var="BOATRACE_ALLOW_ACCIDENT_PROD_WRITE",
        )
    try:
        summary = build_and_compare(args.date, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 - classify upstream cron failures
        failure = {
            "status": "error",
            "message": "external accident preflight failed",
            "check_date": args.date,
            "dry_run": args.dry_run,
            "writes_performed": False,
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
        print(json.dumps(failure, ensure_ascii=False, indent=2))
        if not args.dry_run and not args.no_write_status:
            try:
                with db_connect() as conn:
                    upsert_status(
                        conn,
                        CHECK_NAME,
                        args.date,
                        "error",
                        failure["message"],
                        failure,
                    )
            except Exception as status_exc:  # noqa: BLE001
                logger.error(
                    "failed to persist external accident error status: %s",
                    type(status_exc).__name__,
                )
        return 2
    status, message = status_from_summary(summary)
    print(json.dumps({"status": status, "message": message, **summary}, ensure_ascii=False, indent=2))

    if not args.dry_run and not args.no_write_status:
        with db_connect() as conn:
            upsert_status(conn, CHECK_NAME, args.date, status, message, summary)
    return 0 if status in {"ok", "warning"} else 1


def _notify_failure(message: str, detail: dict) -> None:
    try:
        notify_cron_failure(CRON_JOB_NAME, message, detail=detail)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "accident-external failure mail skipped: %s: %s",
            type(exc).__name__,
            exc,
        )


def main() -> int:
    try:
        exit_code = _main_impl()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            _notify_failure(
                "accident-external cron exited before completion",
                {"exit_code": str(exc.code)},
            )
        raise
    except Exception as exc:
        _notify_failure(
            f"accident-external cron raised {type(exc).__name__}: {exc}"[:500],
            {"error_type": type(exc).__name__, "error": str(exc)[:1000]},
        )
        raise
    if exit_code:
        _notify_failure(
            "accident-external cron completed with a failure status",
            {"exit_code": exit_code},
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
