"""3連複・2連複の列を既存行へ継ぎ足すスクリプトの検証。"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from scripts.backfill_unordered_payouts import PATCH_COLUMNS, main
from src.features.asof_builder import ALL_COLUMNS, UNORDERED_RESULT_COLUMNS, create_output_schema


def _feature_db(path: Path, race_ids: list[str]) -> Path:
    with sqlite3.connect(path) as connection:
        create_output_schema(connection)
        for race_id in race_ids:
            connection.execute(
                "INSERT INTO asof_race_features (race_id, race_date, asof_date, built_at, "
                "schema_version, result_sanrentan) VALUES (?, '2025-08-01', '2025-07-31', 'x', 11, '1-2-3')",
                (race_id,),
            )
    return path


def _source_db(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE race_results (race_id TEXT, boat_number INTEGER, finishing_position INTEGER)")
        connection.execute("CREATE TABLE race_payouts (race_id TEXT, bet_type TEXT, combination TEXT, payout INTEGER)")
        for race_id in ("doubled", "conflict"):
            connection.executemany(
                "INSERT INTO race_results VALUES (?,?,?)",
                [(race_id, boat, boat) for boat in range(1, 7)],
            )
        connection.executemany(
            "INSERT INTO race_payouts VALUES (?,?,?,?)",
            [
                # 2025-07-15 以降の実データの形: 同じ払戻が 2 表記で二重に入る
                ("doubled", "trio", "1-2-3", 310),
                ("doubled", "trio", "1=2=3", 310),
                ("doubled", "quinella", "1-2", 220),
                ("doubled", "quinella", "1=2", 220),
                ("doubled", "trifecta", "1-2-3", 1230),
                # 金額が食い違う → 判定不能 (NULL) のまま
                ("conflict", "trio", "1-2-3", 310),
                ("conflict", "trio", "1=2=3", 999),
                ("conflict", "quinella", "1-2", 220),
            ],
        )
    return path


def _row(path: Path, race_id: str) -> sqlite3.Row:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT * FROM asof_race_features WHERE race_id = ?", (race_id,)
        ).fetchone()
    finally:
        connection.close()


def test_patch_columns_follow_the_feature_definition_order() -> None:
    """本番は継ぎ当ての列の順に末尾へ足す。定義と違う順だと翌晩の差分が拒否される。"""
    names = [name for name, _kind in UNORDERED_RESULT_COLUMNS]
    assert list(PATCH_COLUMNS) == names + ["schema_version"]
    assert [name for name, _kind in ALL_COLUMNS][-len(names):] == names


def test_backfill_fills_new_columns_and_moves_rows_to_v12(tmp_path: Path) -> None:
    features = _feature_db(tmp_path / "features.db", ["doubled", "conflict", "no-source"])
    source = _source_db(tmp_path / "source.db")

    assert main(["--db", str(features), "--source", str(source)]) == 0

    doubled = _row(features, "doubled")
    assert doubled["schema_version"] == 12
    assert doubled["result_sanrenpuku"] == "1-2-3"
    assert doubled["payout_sanrenpuku"] == 310            # 620 ではない
    assert json.loads(doubled["payout_sanrenpuku_json"]) == {"1-2-3": 310}
    assert doubled["result_nirenpuku"] == "1-2"
    assert json.loads(doubled["payout_nirenpuku_json"]) == {"1-2": 220}
    assert doubled["result_sanrentan"] == "1-2-3"         # ほかの列には触れない

    conflict = _row(features, "conflict")
    assert conflict["schema_version"] == 12
    assert conflict["result_sanrenpuku"] is None
    assert conflict["payout_sanrenpuku_json"] is None
    assert conflict["result_nirenpuku"] == "1-2"

    no_source = _row(features, "no-source")
    assert no_source["schema_version"] == 12
    assert no_source["result_sanrenpuku"] is None

    # 2 回目は対象なし (冪等)
    assert main(["--db", str(features), "--source", str(source)]) == 0


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    features = _feature_db(tmp_path / "features.db", ["doubled"])
    source = _source_db(tmp_path / "source.db")
    before = features.read_bytes()

    assert main(["--db", str(features), "--source", str(source), "--dry-run"]) == 0

    assert features.read_bytes() == before
    assert _row(features, "doubled")["schema_version"] == 11


def test_refuses_to_write_into_the_source_database(tmp_path: Path) -> None:
    source = _source_db(tmp_path / "boatrace.db")
    assert main(["--db", str(source), "--source", str(source)]) == 2
