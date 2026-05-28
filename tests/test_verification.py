"""検証エージェントの単体テスト (抽出 + Tier 判定)。

ネットワーク/DB に依存する箇所はテストしない (extract と Tier ロジックのみ)。
バックテスト関数 (DB クエリ) は src/verification/backtest.py の純粋
ヘルパー (_tier, unsupported_conditions) のみ対象とする。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(modpath: str):
    spec = importlib.util.spec_from_file_location(
        modpath.replace("/", "."), ROOT / modpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_extract_venue_and_class():
    from src.verification.extract import extract_single
    text = "桐生のA1選手は1コースを取れば鉄板。3連単1-2-3。"
    m = extract_single(text)
    assert m is not None
    cond = m["conditions"]
    assert cond.get("stadium") == [1]
    assert cond.get("racer_class") == [1]
    assert cond.get("course") == [1]
    assert cond.get("bet_type") == "trifecta"
    assert cond.get("finish_pattern") == "1-2-3"


def test_extract_wind_and_st():
    from src.verification.extract import extract_single
    text = "桐生では追い風でかつ4コースのスタート巧者が来ると勝ちやすい"
    m = extract_single(text)
    assert m is not None
    cond = m["conditions"]
    assert cond["stadium"] == [1]
    assert cond["wind_direction"] == "tailwind"
    assert cond["course"] == [4]
    # "スタート巧者" → デフォ ST 0.16
    assert cond["racer_avg_st_max"] == 0.16


def test_extract_rejects_thin_text():
    from src.verification.extract import extract_single
    # 条件 2 個未満ならノイズとして None
    m = extract_single("レースは難しいですね")
    assert m is None


def test_extract_st_explicit_value():
    from src.verification.extract import extract_single
    text = "桐生でST0.13以下の選手は強い。3連単"
    m = extract_single(text)
    assert m is not None
    assert m["conditions"]["racer_avg_st_max"] == 0.13


def test_extract_weather_exclude():
    from src.verification.extract import extract_single
    text = "桐生 1コース 雨を除外 3連単"
    m = extract_single(text)
    assert m is not None
    assert m["conditions"]["weather_exclude"] == [3]


def test_extract_methods_multi_paragraph():
    from src.verification.extract import extract_methods
    text = """桐生のA1×追い風 1コース まくり 3連単

戸田で2コースのST巧者は鉄板 3連単 1-2-3"""
    methods = extract_methods(text, "http://example.com")
    assert len(methods) == 2
    assert methods[0]["conditions"]["stadium"] == [1]
    assert methods[1]["conditions"]["stadium"] == [2]


def test_tier_judgment():
    from src.verification.backtest import _tier
    assert _tier(160, 200) == "tier_1"
    assert _tier(160, 80) == "tier_2"  # ROIは1だが n<100 でtier_2
    assert _tier(130, 60) == "tier_2"
    assert _tier(110, 40) == "tier_3"
    assert _tier(90, 200) == "discard"
    assert _tier(200, 20) == "insufficient_sample"  # n<30 はROI高くてもinsufficient


def test_unsupported_conditions_flags_wind_direction():
    from src.verification.backtest import unsupported_conditions
    cond = {"stadium": [1], "wind_direction": "tailwind"}
    unsupp = unsupported_conditions(cond)
    # 風向は SQL に落とせない
    assert any("wind_direction" in u for u in unsupp)


def test_title_of_combines_conditions():
    from src.verification.extract import title_of
    m = {"conditions": {"stadium": [1], "wind_direction": "tailwind",
                        "course": [4], "racer_class": [1]}}
    t = title_of(m)
    assert "桐生" in t
    assert "追い風" in t
    assert "4コース" in t
    assert "A1" in t
