from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from src.features.asof_builder import create_output_schema
from src.kachisuji_web.app import create_app


def _row(race_id: str, race_date: str, **overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "race_id": race_id,
        "race_date": race_date,
        "asof_date": race_date,
        "built_at": "2026-08-15T00:00:00+00:00",
        "schema_version": 2,
        "jcd": 14,
        "race_no": 1,
        "weather": "晴",
        "wind_speed": 2.5,
        "tide_phase": "満潮前後",
        "female_present": 0,
        "class_mix": "A1単騎",
        "day_index": "初日",
        "daypart": "デイ",
        "b1_class": "A1",
        "b1_racer_id": 4320,
        "b1_age": 30,
        "b1_avg_st": 0.12,
        "b1_national_rate": 7.1,
        "b1_local_rate": 6.8,
        "b1_national_rate2": 45.0,
        "b1_local_rate2": 40.0,
        "b1_motor_rate2": 42.0,
        "b1_ex_time": 6.70,
        "b1_ex_rank": 1,
        "b1_ex_dev": -0.15,
        "b1_ex_st": 0.08,
        "b1_kimarite_rate_nige": 70.0,
        "b1_accident_rate": 0.4,
        "b2_age": 35,
        "b2_avg_st": 0.15,
        "b2_national_rate": 5.5,
        "b2_local_rate": 5.0,
        "b2_national_rate2": 35.0,
        "b2_local_rate2": 30.0,
        "b2_motor_rate2": 35.0,
        "b2_ex_time": 6.80,
        "b2_ex_st": 0.10,
        "result_tansho": 1,
        "payout_tansho": 180,
        "result_nirentan": "1-2",
        "payout_nirentan": 650,
        "result_sanrentan": "1-2-3",
        "payout_sanrentan": 1230,
    }
    row.update(overrides)
    return row


@pytest.fixture
def fixture_db(tmp_path: Path) -> Path:
    path = tmp_path / "kachisuji-web.db"
    rows = [
        _row("r1", "2024-12-10"),
        _row(
            "r2",
            "2025-01-10",
            weather="曇",
            b1_class="A2",
            b1_racer_id=5000,
            b1_motor_rate2=30.0,
            b1_ex_rank=5,
            result_tansho=2,
            payout_tansho=260,
            result_nirentan="2-1",
            payout_nirentan=900,
            result_sanrentan="2-1-3",
            payout_sanrentan=2100,
        ),
        _row(
            "r3",
            "2025-02-10",
            result_tansho=None,
            payout_tansho=None,
            result_nirentan=None,
            payout_nirentan=None,
            result_sanrentan=None,
            payout_sanrentan=None,
        ),
    ]
    with sqlite3.connect(path) as conn:
        create_output_schema(conn)
        for row in rows:
            columns = list(row)
            conn.execute(
                f"INSERT INTO asof_race_features ({','.join(columns)}) "
                f"VALUES ({','.join('?' for _ in columns)})",
                [row[column] for column in columns],
            )
    return path


@pytest.fixture
def client(fixture_db: Path):
    app = create_app(fixture_db)
    app.config.update(TESTING=True)
    return app.test_client()


def test_index_renders_major_condition_fields(client) -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for marker in (
        "勝ち筋サーチ",
        'id="venue"',
        'id="betType"',
        'id="weatherChips"',
        'id="windStrength"',
        'id="classMix"',
        'id="raceNoFrom"',
        'id="boats"',
        'id="compareRows"',
        'id="oddsEnabled"',
        'id="oddsSnapshot"',
        'id="conditionCount"',
        'id="miniKpi"',
        "全国2連対率",
        "⚖ 艇間比較",
        'id="dateFrom"',
        'id="dateTo"',
        "条件判定不能で除外した件数",
    ):
        assert marker in html


def test_api_rejects_odds_for_non_trifecta_with_japanese_guidance(client) -> None:
    response = client.post(
        "/api/search",
        json={"bet": {"type": "tansho", "first": 1}, "odds": {"min": 5}},
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "オッズ条件は現在3連単のみ対応しています（単勝・2連単のオッズは未収集）"


def test_search_returns_expected_step2_json_structure(client) -> None:
    response = client.post(
        "/api/search",
        json={"bet": {"type": "tansho", "first": 1}, "fast": True},
    )

    assert response.status_code == 200
    result = response.get_json()
    assert set(result) == {
        "n",
        "hits",
        "hit_rate",
        "roi",
        "roi_ci_low",
        "roi_ci_high",
        "excluded",
        "yearly",
        "warnings",
        "effective_date_range",
    }
    assert result["n"] == 2
    assert result["hits"] == 1
    assert result["excluded"] == {"result_missing": 1, "condition_null": 0}


@pytest.mark.parametrize("unknown_key", ["unknown", "未知キー"])
def test_unknown_condition_key_returns_400(client, unknown_key: str) -> None:
    response = client.post("/api/search", json={unknown_key: True})

    assert response.status_code == 400
    assert response.get_json()["error"] == "入力内容に誤りがあります。各項目の値を確認してください"


def test_duplicate_ticket_returns_japanese_guidance(client) -> None:
    response = client.post(
        "/api/search",
        json={"bet": {"type": "sanrentan", "first": 1, "second": 1, "third": 3}},
    )

    assert response.status_code == 400
    assert "着順ごとに異なる艇番" in response.get_json()["error"]


def test_same_boat_comparison_returns_japanese_guidance(client) -> None:
    response = client.post(
        "/api/search",
        json={
            "compare": [
                {"metric": "age", "boat": 1, "op": "ge", "other": 1, "margin": 0}
            ]
        },
    )

    assert response.status_code == 400
    message = response.get_json()["error"]
    assert "同じ艇同士は比較できません" in message
    assert "compare.0.boat" not in message


@pytest.mark.parametrize(
    "bet",
    [
        {"type": "tansho", "first": 1},
        {"type": "nirentan", "first": 1, "second": 2},
        {"type": "sanrentan", "first": 1, "second": 2, "third": 3},
    ],
)
def test_all_three_bet_types_search_through_api(client, bet: dict[str, object]) -> None:
    response = client.post("/api/search", json={"bet": bet, "fast": True})

    assert response.status_code == 200
    assert response.get_json()["hits"] == 1


def test_boat_class_motor_and_exhibition_conditions_apply_through_api(client) -> None:
    response = client.post(
        "/api/search",
        json={
            "bet": {"type": "tansho", "first": 1},
            "boats": {
                "1": {
                    "class": ["A1"],
                    "motor_rate2": {"min": 40},
                    "ex_rank": {"min": 1, "max": 3},
                }
            },
            "fast": True,
        },
    )

    assert response.status_code == 200
    result = response.get_json()
    assert result["n"] == 1
    assert result["hits"] == 1
    assert result["roi"] == 180.0


def test_step5_ranges_and_compare_apply_through_api(client) -> None:
    response = client.post(
        "/api/search",
        json={
            "bet": {"type": "tansho", "first": 1},
            "race_no": {"min": 1, "max": 12},
            "boats": {
                "1": {
                    "age": {"max": 32},
                    "national_rate2": {"min": 40},
                    "local_rate2": {"min": 35},
                }
            },
            "compare": [
                {
                    "metric": "motor_rate2",
                    "boat": 1,
                    "op": "ge",
                    "other": 2,
                    "margin": 5,
                }
            ],
            "fast": True,
        },
    )

    assert response.status_code == 200
    assert response.get_json()["n"] == 1


def test_number_and_number_plus_name_racer_inputs_are_supported(client) -> None:
    for racer in ("4320", "4320 峰竜太"):
        response = client.post(
            "/api/search",
            json={
                "bet": {"type": "tansho", "first": 1},
                "boats": {"1": {"racer_id": racer}},
                "fast": True,
            },
        )
        assert response.status_code == 200
        assert response.get_json()["n"] == 1


def test_name_only_racer_input_returns_actionable_400(client) -> None:
    response = client.post(
        "/api/search",
        json={"boats": {"1": {"racer_id": "峰竜太"}}, "fast": True},
    )

    assert response.status_code == 400
    assert "選手番号で指定してください" in response.get_json()["error"]


def test_healthz(client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_double_click_launcher_is_ascii_and_has_required_guards() -> None:
    launcher = Path(__file__).resolve().parents[1] / "scripts" / "start_kachisuji.bat"
    content = launcher.read_bytes()
    text = content.decode("ascii")

    assert "http://127.0.0.1:8080/healthz" in text
    assert "scripts\\run_kachisuji_web.py --port 8080" in text
    assert "start \"\" http://localhost:8080" in text
    assert "Closing this window stops the server" in text
