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

    return {
        "boats": boats,
        "payouts": payouts,
        "race_kimarite": None,
    }
