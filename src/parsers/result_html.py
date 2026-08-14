"""boatrace.jp の /owpc/pc/race/raceresult HTML パーサー

Open API (boatraceopenapi.github.io) は数時間〜半日遅延で更新される
バッチ式のため、レース終了直後の結果はここから直接スクレイプして補う。

戻り値は src.collectors.openapi.upsert_results が期待する形式の dict。
未確定 (まだ走っていない/結果ページが空) の場合は None。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 全角数字 → 半角
ZEN_TO_HALF = {
    "１": 1, "２": 2, "３": 3, "４": 4, "５": 5, "６": 6,
}

# 公式の決まり手 6 種。これ以外の文字列は誤検出とみなして採用しない。
# 「まくり差し」は「まくり」「差し」を含むため、最長一致になるよう先頭に置く。
KIMARITE_VALUES = ("まくり差し", "まくり", "逃げ", "差し", "抜き", "恵まれ")

# boatrace.jp 表記 → Open API bet_type 名 (race_payouts.bet_type に保存される名前)
BET_TYPE_MAP = {
    "3連単": "trifecta",
    "3連複": "trio",
    "2連単": "exacta",
    "2連複": "quinella",
    "拡連複": "quinella_place",
    "単勝": "win",
    "複勝": "place",
}


def _parse_int(s: str) -> Optional[int]:
    if not s:
        return None
    digits = re.sub(r"[^0-9]", "", s)
    return int(digits) if digits else None


def _normalize_combination(s: str) -> str:
    """boatrace.jp の '1-3-2' / '1=2=3' をそのまま返す (Open API 互換)。"""
    return re.sub(r"\s+", "", s)


def parse_kimarite(soup: BeautifulSoup) -> Optional[str]:
    """結果ページから決まり手 (逃げ/差し/まくり/まくり差し/抜き/恵まれ) を抽出。

    boatrace.jp の raceresult ページでは、以下のような小テーブルで表示される:

        <table class="is-w243 h-mt10">
          <thead><tr><th>決まり手</th></tr></thead>
          <tbody><tr><td>逃げ</td></tr></tbody>
        </table>

    安全弁: どんな HTML でも例外を投げず、確実に抽出できた場合のみ
    KIMARITE_VALUES のいずれかを返す。それ以外は None。
    """
    try:
        for t in soup.find_all("table"):
            thead = t.find("thead")
            if not thead or "決まり手" not in thead.get_text():
                continue
            body = t.find("tbody") or t
            for cell in body.find_all("td"):
                text = cell.get_text(strip=True)
                for value in KIMARITE_VALUES:
                    if text == value:
                        return value
        # フォールバック: テーブル構造が変わった場合はテキスト近傍から抽出
        page_text = soup.get_text(" ", strip=True)
        m = re.search(
            r"決まり手\s*(" + "|".join(KIMARITE_VALUES) + r")",
            page_text,
        )
        if m:
            return m.group(1)
    except Exception as exc:  # noqa: BLE001 — パース失敗でも結果取得全体は続行
        logger.warning("kimarite parse failed: %s", exc)
    return None


def parse_result_html(html: str) -> Optional[dict]:
    """raceresult HTML をパース。
    Returns:
        {"boats": [...], "payouts": {...}, "race_kimarite": str|None}
        まだ結果が出ていない場合は None
    """
    if not html or "raceresult" not in html.lower() and "勝式" not in html:
        # 単純な存在チェック
        pass
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")

    finish_table = None
    payouts_table = None
    for t in tables:
        head_text = t.find("thead").get_text() if t.find("thead") else ""
        if not finish_table and "着" in head_text and "枠" in head_text:
            finish_table = t
        elif not payouts_table and "勝式" in head_text and "組番" in head_text:
            payouts_table = t

    if not finish_table:
        return None

    boats: list[dict] = []
    for tr in finish_table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
        if not cells or cells[0] in ("着",):
            continue  # thead row
        if len(cells) < 2:
            continue
        place_str = cells[0]
        # 着が空欄 = 結果未確定
        if place_str not in ZEN_TO_HALF:
            continue
        place = ZEN_TO_HALF[place_str]
        # 枠 cell は boat number (1-6)
        boat_str = cells[1]
        boat_num = _parse_int(boat_str)
        if boat_num is None:
            continue
        # レースタイム (5位以下は空欄)
        race_time = cells[3] if len(cells) > 3 and cells[3] else None
        boats.append({
            "racer_boat_number": boat_num,
            "racer_place_number": place,
            "racer_race_time": race_time,
        })

    if not boats:
        # 結果テーブルはあったが、データ行が無い → 未確定
        return None

    # 払戻金
    payouts: dict[str, list[dict]] = {v: [] for v in BET_TYPE_MAP.values()}
    if payouts_table:
        current_bet_type: Optional[str] = None
        for tr in payouts_table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            texts = [c.get_text(strip=True) for c in cells]
            if texts[0] in ("勝式", "組番", "払戻金", "人気"):
                continue  # header

            # 行の先頭セルが bet_type ラベルかどうか判定
            label_jp = texts[0] if texts[0] in BET_TYPE_MAP else None
            if label_jp:
                current_bet_type = BET_TYPE_MAP[label_jp]
                # combination / payout / popularity は次の 3 セル
                combo = texts[1] if len(texts) > 1 else ""
                payout = texts[2] if len(texts) > 2 else ""
                pop = texts[3] if len(texts) > 3 else ""
            else:
                # 継続行 (拡連複や複勝の 2行目以降): 先頭セルが combo
                if current_bet_type is None:
                    continue
                combo = texts[0]
                payout = texts[1] if len(texts) > 1 else ""
                pop = texts[2] if len(texts) > 2 else ""

            # 完全に空のパディング行はスキップ
            if not combo and not payout:
                continue

            payout_amount = _parse_int(payout)
            if payout_amount is None or payout_amount <= 0:
                continue

            popularity = _parse_int(pop)
            payouts[current_bet_type].append({
                "combination": _normalize_combination(combo),
                "payout": payout_amount,
                "popularity": popularity,
            })

    # ========================================================
    # 水面気象情報 (div.weather1) — レース時の確定値
    # beforeinfo の生値と同じ構造で取れる。post-race overwrite で
    # race_previews の朝予報を確定値に置き換えるために使う。
    # ========================================================
    weather = {
        "weather_number": None,
        "wind_speed": None,
        "wind_direction_number": None,
        "wave_height": None,
        "temperature": None,
        "water_temperature": None,
    }
    weather_panel = soup.select_one("div.weather1")
    if weather_panel:
        ptxt = weather_panel.get_text(separator=" ")
        m = re.search(r"気温\s*([-\d.]+)", ptxt)
        if m:
            try:
                weather["temperature"] = float(m.group(1))
            except ValueError:
                pass
        m = re.search(r"水温\s*([-\d.]+)", ptxt)
        if m:
            try:
                weather["water_temperature"] = float(m.group(1))
            except ValueError:
                pass
        m = re.search(r"風速\s*([\d.]+)", ptxt)
        if m:
            try:
                weather["wind_speed"] = int(float(m.group(1)))
            except ValueError:
                pass
        m = re.search(r"波高?\s*([\d.]+)", ptxt)
        if m:
            try:
                weather["wave_height"] = int(float(m.group(1)))
            except ValueError:
                pass
        for img in weather_panel.select("p.weather1_bodyUnitImage"):
            cls_attr = " ".join(img.get("class", []))
            m = re.search(r"is-weather(\d+)\b", cls_attr)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 5:
                    weather["weather_number"] = n
            m = re.search(r"is-wind(\d+)\b", cls_attr)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 17:
                    weather["wind_direction_number"] = n

    return {
        "boats": boats,
        "payouts": payouts,
        "race_kimarite": parse_kimarite(soup),
        "weather": weather,
    }
