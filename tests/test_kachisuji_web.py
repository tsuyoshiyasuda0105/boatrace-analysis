from __future__ import annotations

from pathlib import Path
import re
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
        conn.execute(
            "CREATE TABLE racers (racer_number INTEGER PRIMARY KEY, name TEXT, name_kana TEXT)"
        )
        conn.executemany(
            "INSERT INTO racers VALUES (?, ?, ?)",
            [
                (4190, "長嶋万記", "ﾅｶﾞｼﾏ ﾏｷ"),
                (4320, "峰竜太", "ﾐﾈ ﾘｭｳﾀ"),
                (4714, "喜多須杏奈", "ｷﾀｽﾞ ｱﾝﾅ"),
                (5000, "赤峰和也", "ｱｶﾐﾈ ｶｽﾞﾔ"),
            ],
        )
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
        'id="venueAll"',
        'id="venueCount"',
        'id="betType"',
        'id="weatherChips"',
        'id="windStrength"',
        'id="windDirection"',
        'id="classMix"',
        'value="1号艇A1・単騎"',
        'value="1号艇A1・複数"',
        'value="1号艇非A1・A1あり"',
        'value="A1なし"',
        'id="raceNoFrom"',
        'id="boats"',
        'class="racer-input"',
        'role="combobox"',
        'aria-autocomplete="list"',
        'role="listbox"',
        'aria-expanded="false"',
        'id="compareRows"',
        'id="conditionCount"',
        'class="condition-summary"',
        'class="discovery-status discovery-',
        'function discoveryStatusHtml(data)',
        'function strategyGrowthHtml(item)',
        'レベルは検証したレース数を表します。成績は判定バッジで確認します。',
        'id="miniKpi"',
        "全国2連対率",
        "事故率（審査期・本日判定用）",
        "事故率（審査期・検証用）",
        "事故件数（審査期・検証用）",
        "事故点（審査期）",
        "事故率（過去1年・参考）",
        "2016/6〜・推奨",
        "平均ST（直近180日）",
        "決まり手 勝率（進入コース時）",
        "1コースに入ったときの逃げ率など、そのコースに入ったときの決まり手成功率です。",
        "F・L・欠測を除いた有効走数 n",
        "⚖ 艇間比較",
        'id="dateFrom"',
        'id="dateTo"',
        "条件判定不能で除外した件数",
        "検索条件: 指定なし（全レース）",
    ):
        assert marker in html

    assert 'value="A1が2人以上"' not in html
    assert 'value="A1単騎"' not in html
    assert 'value="1号艇A1"' not in html
    class_mix_block = re.search(
        r'<select id="classMix">(.*?)</select>', html, re.DOTALL
    )
    assert class_mix_block is not None
    assert re.findall(r'<option value="([^"]*)">', class_mix_block.group(1)) == [
        "",
        "1号艇A1・単騎",
        "1号艇A1・複数",
        "1号艇非A1・A1あり",
        "A1なし",
    ]
    for removed_marker in (
        'id="oddsEnabled"',
        'id="oddsMin"',
        'id="oddsMax"',
        'id="favoriteOddsEnabled"',
        'id="favoriteOddsMin"',
        'id="favoriteOddsMax"',
        "3連単オッズ",
        "人気帯",
        "T-5",
    ):
        assert removed_marker not in html


@pytest.mark.parametrize("endpoint", ["/api/search", "/api/strategies"])
@pytest.mark.parametrize(
    "retired_condition",
    [
        {"odds": {"snapshot": "T-5min", "min": 5}},
        {"t5_odds_favorite": {"min": 5}},
    ],
)
def test_api_rejects_retired_odds_conditions_with_japanese_guidance(
    client, endpoint: str, retired_condition: dict[str, object]
) -> None:
    conditions = {
        "bet": {"type": "sanrentan", "first": 1, "second": 2, "third": 3},
        **retired_condition,
    }
    payload = conditions if endpoint == "/api/search" else {"name": "旧オッズ手法", "conditions": conditions}

    response = client.post(endpoint, json=payload)

    assert response.status_code == 400
    assert response.get_json()["error"] == (
        "オッズによる絞り込みは廃止されました。"
        "回収率は条件に合う全レースを分母に計算します"
    )


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
        "monthly",
        "warnings",
        "effective_date_range",
        # 複数買い目対応で追加。合算 ROI だけだとどの目が効いているか分からない
        # ため、点数・投資額・目ごとの内訳を返す。
        "ticket_count",
        "stake_total",
        "tickets",
        "ticket_breakdown",
        # 季節 (春夏秋冬) 別の内訳。race_date から導出するので常に返る。
        "seasonal",
    }
    assert result["ticket_count"] == 1
    assert result["tickets"] == ["1"]
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


@pytest.mark.parametrize("query", ["峰", "竜太", "峰竜", "みね", "ミネ", "ﾐﾈ", "りゅうた"])
def test_racer_api_matches_name_and_normalized_kana_parts(client, query: str) -> None:
    response = client.get("/api/racers", query_string={"q": query})

    assert response.status_code == 200
    assert any(item["racer_number"] == 4320 for item in response.get_json())


@pytest.mark.parametrize("query", ["", " ", "a", "み", "%", "_"])
def test_racer_api_rejects_too_short_non_kanji_queries(client, query: str) -> None:
    response = client.get("/api/racers", query_string={"q": query})

    assert response.status_code == 200
    assert response.get_json() == []


def test_racer_api_applies_limit_and_caps_it_at_fifty(client) -> None:
    limited = client.get("/api/racers", query_string={"q": "ミネ", "limit": 1})
    capped = client.get("/api/racers", query_string={"q": "ミネ", "limit": 999})

    assert len(limited.get_json()) == 1
    assert len(capped.get_json()) <= 50


def test_racer_api_invalid_limit_falls_back_without_error(client) -> None:
    response = client.get("/api/racers", query_string={"q": "ミネ", "limit": "bad"})

    assert response.status_code == 200
    assert any(item["racer_number"] == 4320 for item in response.get_json())


@pytest.mark.parametrize("query", ["' OR 1=1 --", "峰%", "峰_", "不存在選手"])
def test_racer_api_treats_injection_and_wildcards_as_literal_text(client, query: str) -> None:
    response = client.get("/api/racers", query_string={"q": query})

    assert response.status_code == 200
    assert response.get_json() == []


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
