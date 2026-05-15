"""
B (番組表) ファイルパーサー — Layer 1

ファイル構造 (cp932/SJIS, 固定幅テキスト):
  STARTB / YYBBGN ヘッダ → 各会場ブロック → ENDB

会場ブロック検出: 'ボートレース<会場名>' で始まるヘッダ行
レース検出: '   1R' / '   ２Ｒ' のような full/half-width race header
艇行: '1 3773谷川 翔太郎49東京55B1 4.85 30.59 5.04 31.31 57 25.58 25 37.04 ...'

CLASS_MAP: A1/A2/B1/B2 → 1/2/3/4
"""
from __future__ import annotations

import re
import logging
import unicodedata
from datetime import date as _date
from typing import Optional

import config

logger = logging.getLogger(__name__)


CLASS_NUM = {"A1": 1, "A2": 2, "B1": 3, "B2": 4}


def _to_half(s: str) -> str:
    """全角英数字を半角化"""
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


# ============================================================
# 1艇行のパース
# ============================================================

# pattern: lane(1) sp racer(4) name age(2) branch(2chars) weight(2) class(2)
#          national_win national_top2 local_win local_top2 motor_no motor_top2 boat_no boat_top2
ROW_RE = re.compile(
    r"^([1-6])\s+(\d{4})(.+?)(\d{2})(\S{2})(\d{2})([AB][12])"
    r"\s+(-?\d+\.\d{2})\s+(\d+\.\d{2})"
    r"\s+(-?\d+\.\d{2})\s+(\d+\.\d{2})"
    r"\s+(\d+)\s+(\d+\.\d{2})"
    r"\s+(\d+)\s+(\d+\.\d{2})"
)


def _parse_boat_row(line: str) -> Optional[dict]:
    m = ROW_RE.match(line)
    if not m:
        return None
    return {
        "boat_number": int(m.group(1)),
        "racer_number": int(m.group(2)),
        "racer_name": m.group(3).strip().replace("　", " "),
        "age": int(m.group(4)),
        "branch_name": m.group(5),  # 漢字、後で master 突合
        "weight": float(m.group(6)),
        "class_number": CLASS_NUM.get(m.group(7).upper()),
        "national_top_1_percent": float(m.group(8)),
        "national_top_2_percent": float(m.group(9)),
        "local_top_1_percent": float(m.group(10)),
        "local_top_2_percent": float(m.group(11)),
        "assigned_motor_number": int(m.group(12)),
        "assigned_motor_top_2_percent": float(m.group(13)),
        "assigned_boat_number": int(m.group(14)),
        "assigned_boat_top_2_percent": float(m.group(15)),
        "flying_count": None,
        "late_count": None,
        "avg_start_timing": None,
    }


# ============================================================
# レース番号抽出 (全角/半角どちらにも対応)
# ============================================================

RACE_HEADER_RE = re.compile(r"^\s*(\d{1,2})R\b")


def _extract_race_no(line: str) -> Optional[int]:
    """' １Ｒ  一般...' '   1R  一般...' から 1 を取得"""
    norm = _to_half(line)
    m = RACE_HEADER_RE.match(norm)
    if m:
        n = int(m.group(1))
        if 1 <= n <= 12:
            return n
    return None


# ============================================================
# 会場名抽出
# ============================================================

STADIUM_RE = re.compile(r"ボートレース\s*(.+?)\s*$")


def _extract_stadium(line: str, stadium_map: dict[str, int]) -> Optional[int]:
    """
    'ボートレース桐 生' / 'ボートレース　住之江' → stadium_number
    """
    m = STADIUM_RE.search(line)
    if not m:
        return None
    raw = m.group(1).replace("　", "").replace(" ", "")
    # 完全一致を最初に
    if raw in stadium_map:
        return stadium_map[raw]
    # 部分一致 (例: 桐生 と 桐 生 / びわこ と 琵琶湖)
    for nm, sn in stadium_map.items():
        if raw == nm or nm in raw or raw in nm:
            return sn
    return None


# ============================================================
# 公開関数
# ============================================================

# ============================================================
# 大会名 → グレード推測 (race_grade_number 推定)
#  1 = SG, 2 = G1, 3 = G2, 4 = G3, 5 = 一般戦
# ============================================================
GRADE_KEYWORDS: list[tuple[int, list[str]]] = [
    # SG (賞金王・グランプリ・チャレンジカップ・ボートレース大賞・ダービー・
    #     クラシック・オールスター・総理大臣・全日本選手権)
    (1, ["グランプリ", "クラシック", "ＳＧ", "賞金王", "総理大臣",
         "全日本選手権", "鳳凰賞", "オールスター", "ボートレース大賞",
         "ダービー", "チャレンジカップ"]),
    # G1 (各場周年・グランドチャンピオン・海の祭典・モーターボート記念)
    (2, ["Ｇ１", "周年記念", "グランドチャンピオン", "海の祭典",
         "モーターボート記念", "メモリアル"]),
    # G2 (クイーンズクライマックス・レディースチャレンジ・ヤングダービー・
    #     モーターボート大賞 等)
    (3, ["Ｇ２", "クイーンズクライマックス", "レディースチャレンジ",
         "ヤングダービー", "マスターズチャンピオン"]),
    # G3 (企業杯、◯◯記念、レディースカップ、ヤングシリーズ等)
    (4, ["Ｇ３", "ヤング", "レディース", "マスターズ", "ルーキー",
         "企業杯", "ヴィーナス"]),
]


def _guess_grade(tournament_title: str) -> Optional[int]:
    """大会名タイトルからグレードを推定。
    マッチしなければ 5 (一般戦)。空文字なら None。
    """
    if not tournament_title:
        return None
    for grade, kws in GRADE_KEYWORDS:
        for kw in kws:
            if kw in tournament_title:
                return grade
    return 5  # 何も match しない → 一般戦


def _extract_tournament_title(line: str) -> Optional[str]:
    """B ファイルのヘッダ行から大会名を抽出。
    例: 'ボートレース大村   １月１日  ミッドナイトボートレ  第１日'
        → 'ミッドナイトボートレ'
    """
    # 「ボートレース<会場>」 + 「<日付>」 + 「<大会名>」 + 「第N日」 の構造
    # 区切りは多数の全角/半角スペース
    if not ("ボートレース" in line and "第" in line and "日" in line):
        return None
    # 「第N日」より前の部分から大会名を取る
    m = re.search(r"ボートレース.+?[０-９0-9]+月.+?日\s+(.+?)\s+第", line)
    if m:
        return m.group(1).strip()
    return None


def parse_b_text(text: str, target_date: _date) -> list[dict]:
    """
    B ファイル全体をパースして races のリストを返す。

    各要素:
      {
        race_id, race_date, stadium_number, race_number,
        race_grade_number, race_title, race_distance,
        boats: [ {boat_number, racer_number, racer_name, ...}, ... 6 ]
      }
    """
    stadium_map = _stadium_name_to_number()
    out: list[dict] = []

    lines = text.splitlines()
    cur_stadium: Optional[int] = None
    cur_race: Optional[dict] = None
    # 会場ごとの大会名 → grade マップ (同じ B ファイル内で複数会場混在)
    stadium_grade: dict[int, Optional[int]] = {}

    for line in lines:
        # 会場検出 + 大会名抽出 (新会場ブロック)
        sn = _extract_stadium(line, stadium_map)
        if sn is not None:
            cur_stadium = sn
            # 同じ行内の大会名から grade 推定 (まだ grade 未取得時のみ; 重複行で
            # None で上書きされないように)
            if stadium_grade.get(sn) is None:
                title = _extract_tournament_title(line)
                if title:
                    stadium_grade[sn] = _guess_grade(title)
            continue

        # レース番号検出
        rno = _extract_race_no(line)
        if rno is not None and cur_stadium is not None:
            # 進行中のレースを締め
            if cur_race is not None and len(cur_race["boats"]) > 0:
                out.append(cur_race)
            # レースタイトル
            title = _to_half(line.strip())
            # 距離 H1800m → 1800
            dist_m = re.search(r"[HＨ](\d{4})[mｍ]", title)
            distance = int(dist_m.group(1)) if dist_m else None
            cur_race = {
                "race_id": f"{target_date.strftime('%Y%m%d')}-{cur_stadium:02d}-{rno:02d}",
                "race_date": target_date.isoformat(),
                "stadium_number": cur_stadium,
                "race_number": rno,
                "race_title": title,
                "race_subtitle": None,
                "race_grade_number": stadium_grade.get(cur_stadium),
                "race_distance": distance,
                "race_closed_at": None,
                "boats": [],
            }
            continue

        # 艇行検出
        boat = _parse_boat_row(line)
        if boat is not None and cur_race is not None and len(cur_race["boats"]) < 6:
            cur_race["boats"].append(boat)

    if cur_race is not None and len(cur_race["boats"]) > 0:
        out.append(cur_race)

    # 6艇揃ったレースのみ採用
    return [r for r in out if len(r["boats"]) == 6]
