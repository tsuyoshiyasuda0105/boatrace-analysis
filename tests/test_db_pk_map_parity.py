import re
from pathlib import Path

from src.db.connection import _TABLE_PRIMARY_KEYS


ROOT = Path(__file__).resolve().parents[1]
INSERT_OR_PATTERN = re.compile(
    r"\bINSERT\s+OR\s+(?:REPLACE|IGNORE)\s+INTO\s+([A-Za-z_]\w*)",
    re.IGNORECASE,
)
EXCLUDED_DIRS = {".git", ".venv", "tests", "__pycache__"}


def _production_insert_or_targets():
    targets = {}
    for path in ROOT.rglob("*"):
        if path.suffix.lower() not in {".py", ".sql"}:
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        source = path.read_text(encoding="utf-8", errors="ignore")
        for match in INSERT_OR_PATTERN.finditer(source):
            table = match.group(1).lower()
            if table == "t":  # Generic table name in the shim documentation.
                continue
            targets.setdefault(table, []).append(str(path.relative_to(ROOT)))
    return targets


def test_all_static_insert_or_targets_have_primary_key_map_entries():
    targets = _production_insert_or_targets()
    missing = {
        table: locations
        for table, locations in targets.items()
        if table not in _TABLE_PRIMARY_KEYS
    }

    assert not missing, f"INSERT OR target tables missing PK mappings: {missing}"


def test_newly_registered_primary_keys_match_verified_definitions():
    expected = {
        "l4_daily_summary": ["date"],
        "course1_stats_cache": ["racer_number", "as_of_date"],
        "decay_factor": ["bucket"],
        "paper_trades": ["id"],
        "alert_sent": ["email_hash", "race_id", "alert_type"],
        "racer_entry_change_snapshots": ["snapshot_date", "racer_number"],
    }

    assert {table: _TABLE_PRIMARY_KEYS.get(table) for table in expected} == expected


def test_accident_period_stats_map_matches_production_postgres_primary_key():
    # ON CONFLICT 変換は Postgres 専用。マップは本番 Postgres の実 PK に
    # 一致させること (本番の PK は period_end を含む。ローカル SQLite は
    # 旧スキーマで period_end を欠くが、そちらに合わせると本番で ON CONFLICT が
    # 制約に一致せず事故率パイプラインが壊れる)。2026-08-14 本番 PK で検証済み。
    assert _TABLE_PRIMARY_KEYS["racer_accident_period_stats"] == [
        "racer_number",
        "period_year",
        "period_half",
        "period_end",
        "rule_version",
        "source_kind",
    ]


def test_sync_script_table_list_is_fully_pk_mapped():
    """sync_to_supabase.py は動的 SQL (INSERT OR REPLACE INTO {table}) のため
    静的 grep のパリティテストに映らない。夜間 sync が使う全テーブルが
    PK マップに載っていることを直接検証する (2026-08-15 race_tides 欠落の再発防止)。"""
    nightly_sync_tables = [
        "races", "race_entries", "race_previews", "race_tides",
        "race_original_exhibitions", "predictions", "derived_start_stats",
        "racer_accident_point_rules", "racer_accident_events",
        "racer_accident_period_stats", "racer_accident_rank_snapshots",
    ]
    missing = [t for t in nightly_sync_tables if t not in _TABLE_PRIMARY_KEYS]
    assert not missing, f"nightly sync tables missing PK mappings: {missing}"
