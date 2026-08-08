"""
K (競走成績) ファイルパーサー — Layer 1

ファイル構造 (cp932/SJIS):
  各会場ブロック:
    'ボートレース<会場>' ヘッダ
    トリフェクタ要約テーブル (12レース分のオッズ)
    各レース詳細:
      ' 1R       一般  H1800m  風  方向  風速  Nm  波  Ncm'
      ヘッダ行 (着 艇 登番 選手名 ...)
      6艇結果行: '  01  1 3773 谷川 翔太郎 57   25  6.84   1    0.16     1.49.5'
      払戻金 (単勝/複勝/2連単/2連複/拡連複/3連単/3連複)

注意:
  - 着順位置は2桁、艇番号は1桁、登録番号は4桁、選手名は固定幅日本語、続いて motor/boat 番号、ST、レースタイム
"""
from __future__ import annotations

import re
import logging
import unicodedata
from datetime import date as _date
from typing import Optional

import config

logger = logging.getLogger(__name__)


def _to_half(s: str) -> str:
    return unicodedata.normalize("NFKC", s)


def _stadium_name_to_number() -> dict[str, int]:
    import json
    with open(config.MASTER_DIR / "stadiums.json", encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for key, entry in data.items():
        if not isinstance(entry, dict) or "name" not in entry:
            continue
        nm = entry["name"].replace("　", "").replace(" ", "")
        out[nm] = int(key)
    return out


KIMARITE_VALUES = [
    "まくり差し",
    "逃げ",
    "差し",
    "まくり",
    "抜き",
    "恵まれ",
]


def _extract_kimarite(line: str) -> Optional[str]:
    """Extract the winning method from the result table header line."""
    norm = _to_half(line)
    for value in KIMARITE_VALUES:
        if value in norm:
            return value
    return None


# ============================================================
# 結果行: '  01  1 3773 谷川 翔太郎 57   25  6.84   1    0.16     1.49.5'
# 着順(2) 空白 艇番(1) 空白 登番(4) 空白 選手名(変動) motor_no boat_no time_lap1 course_no st race_time
# ============================================================
RESULT_RE = re.compile(
    r"^\s*(\d{2}|K0|K1|S0|S1|S2|F\s|L\s|失|失格|転|落|妨)\s*"
    r"([1-6])\s+(\d{4})\s+(.+?)\s+(\d+)\s+(\d+)\s+(-?\d+\.\d{2})\s+(\d)\s+(-?[\.\d]+)\s+([\d. ]+)?$"
)


def _parse_result_row(line: str):
    m = RESULT_RE.match(line)
    if not m:
        return None
    rank_raw = m.group(1).strip()
    try:
        finishing_position = int(rank_raw)
    except ValueError:
        finishing_position = None  # F/L/K/失格 等は None
    boat = int(m.group(2))
    racer = int(m.group(3))
    name = m.group(4).strip().replace("　", " ")
    motor_no = int(m.group(5))
    boat_no = int(m.group(6))
    # group 7 は周回タイム的なもの (ETA展示)、現状未使用
    course = int(m.group(8))
    st_str = m.group(9).strip()
    try:
        # F.02 (フライング) は負号で扱う
        if st_str.startswith("F"):
            st = -float("0" + st_str[1:])
        elif st_str.startswith("L"):
            st = None
        else:
            st = float(st_str)
    except ValueError:
        st = None
    race_time = (m.group(10) or "").strip() or None
    return {
        "boat_number": boat,
        "racer_number": racer,
        "racer_name": name,
        "finishing_position": finishing_position,
        "course_number": course,
        "start_timing": st,
        "race_time": race_time,
        "remarks": rank_raw if finishing_position is None else None,
    }


# ============================================================
# レースヘッダ '   1R       一般 ... H1800m 天候 風向 風速 Nm 波 Ncm'
# ============================================================
RACE_HEADER_RE = re.compile(r"^\s*(\d{1,2})R\s")


def _extract_race_no(line: str) -> Optional[int]:
    norm = _to_half(line)
    m = RACE_HEADER_RE.match(norm)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 12:
            return n
    return None


def _parse_weather(line: str) -> dict:
    """レースヘッダから天候・風・波を抽出"""
    norm = _to_half(line)
    out = {
        "wind_speed": None,
        "wave_height": None,
        "weather_text": None,
    }
    # 'Hxxxxm' で距離検出 → その後ろが天候情報
    m = re.search(r"H\d{4}m\s+(\S{1,3})", norm)
    if m:
        out["weather_text"] = m.group(1)
    # 風 Nm
    m = re.search(r"風\s*\S{0,3}\s+(\d+)m", norm)
    if m:
        out["wind_speed"] = int(m.group(1))
    # 波 Ncm
    m = re.search(r"波\s*(\d+)cm", norm)
    if m:
        out["wave_height"] = int(m.group(1))
    return out


# ============================================================
# 会場名検出
# ============================================================

STADIUM_RE = re.compile(r"ボートレース\s*(.+?)\s*$")


def _extract_stadium(line: str, stadium_map: dict[str, int]) -> Optional[int]:
    m = STADIUM_RE.search(line)
    if not m:
        return None
    raw = m.group(1).replace("　", "").replace(" ", "")
    if raw in stadium_map:
        return stadium_map[raw]
    for nm, sn in stadium_map.items():
        if raw == nm or nm in raw or raw in nm:
            return sn
    return None


# ============================================================
# 払戻行: '        単勝     1          110' / '        ３連単   1-4-5     7970'
# ============================================================
# _to_half() を通した後の正規化済テキストに対するパターン
# (「３」→「3」変換されているため regex も半角で書く必要がある)
PAYOUT_PATTERNS = [
    ("win",            re.compile(r"単勝\s+(\d)\s+(\d+)")),
    ("place",          re.compile(r"複勝\s+(\d)\s+(\d+)")),
    ("exacta",         re.compile(r"2連単\s+(\d-\d)\s+(\d+)")),
    ("quinella",       re.compile(r"2連複\s+(\d-\d)\s+(\d+)")),
    ("trifecta",       re.compile(r"3連単\s+(\d-\d-\d)\s+(\d+)")),
    ("trio",           re.compile(r"3連複\s+(\d=\d=\d|\d-\d-\d)\s+(\d+)")),
    ("quinella_place", re.compile(r"拡連複\s+(\d-\d)\s+(\d+)")),
]


def _parse_payouts(lines: list[str]) -> list[dict]:
    out = []
    for line in lines:
        norm = _to_half(line)
        for bet_type, rx in PAYOUT_PATTERNS:
            m = rx.search(norm)
            if m:
                out.append({
                    "bet_type": bet_type,
                    "combination": m.group(1).replace("=", "-"),
                    "payout": int(m.group(2)),
                })
    return out


# ============================================================
# 公開関数
# ============================================================

def parse_k_text(text: str, target_date: _date) -> list[dict]:
    """
    K ファイル全体をパース。各レースは下記のリスト要素として返す。

    {
      race_id, race_date, stadium_number, race_number,
      wind_speed, wave_height, weather_text,
      results: [ {boat_number, finishing_position, ...}, ... 6 ],
      payouts: [ {bet_type, combination, payout}, ... ]
    }
    """
    stadium_map = _stadium_name_to_number()
    out: list[dict] = []

    lines = text.splitlines()
    cur_stadium: Optional[int] = None
    cur_race: Optional[dict] = None
    payout_buffer: list[str] = []

    def _flush():
        if cur_race is not None and len(cur_race["results"]) > 0:
            kim = cur_race.get("kimarite")
            if kim:
                for rr in cur_race["results"]:
                    if rr.get("finishing_position") == 1 and not rr.get("kimarite"):
                        rr["kimarite"] = kim
            cur_race["payouts"] = _parse_payouts(payout_buffer)
            out.append(cur_race)

    for line in lines:
        sn = _extract_stadium(line, stadium_map)
        if sn is not None:
            cur_stadium = sn
            continue

        rno = _extract_race_no(line)
        if rno is not None and cur_stadium is not None and "H" in _to_half(line):
            _flush()
            payout_buffer = []
            weather = _parse_weather(line)
            cur_race = {
                "race_id": f"{target_date.strftime('%Y%m%d')}-{cur_stadium:02d}-{rno:02d}",
                "race_date": target_date.isoformat(),
                "stadium_number": cur_stadium,
                "race_number": rno,
                "wind_speed": weather["wind_speed"],
                "wave_height": weather["wave_height"],
                "weather_text": weather["weather_text"],
                "kimarite": None,
                "results": [],
                "payouts": [],
            }
            continue

        if cur_race is not None and cur_race.get("kimarite") is None:
            kim = _extract_kimarite(line)
            if kim:
                cur_race["kimarite"] = kim
                continue

        # 結果行
        rrow = _parse_result_row(line)
        if rrow is not None and cur_race is not None and len(cur_race["results"]) < 6:
            if rrow.get("finishing_position") == 1 and cur_race.get("kimarite"):
                rrow["kimarite"] = cur_race["kimarite"]
            cur_race["results"].append(rrow)
            continue

        # 払戻バッファ
        if cur_race is not None:
            payout_buffer.append(line)

    _flush()
    return [r for r in out if len(r["results"]) >= 1]
