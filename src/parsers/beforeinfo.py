"""
直前情報ページ HTML パーサー

URL: https://www.boatrace.jp/owpc/pc/race/beforeinfo?jcd=...&hd=YYYYMMDD&rno=R

DOM 構造 (2026-05 時点):
  <table class="is-w748">: 6艇のメインテーブル
    各艇は 6行で構成 (rowspan によるレイアウト):
      行0 (主行): [枠, 写真, 選手, 体重, 展示タイム, チルト, プロペラ, 部品交換, 前走]
      行1-5: 進入/ST/着順 等
  <table class="is-w238">: スタート展示 (コース順に "<艇番号> <ST>" 形式)
  <div class="weather1">: 天候・水温・風

注意:
  - HTML 先頭に <?xml ...?> プロローグがあるので除去してからパース
  - 部品交換セルはレースによって空 (交換無し)
  - F (フライング) はST文字列に "F" 接頭辞で入る
"""
from __future__ import annotations

import logging
import re
import warnings
from typing import Optional, TypedDict

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)


# ============================================================
# 部品名 → schema part_code のマップ
# ============================================================
PART_CODE_MAP = {
    "電気・ガソリン関係": "electric",
    "電気": "electric",
    "ガソリン": "electric",
    "キャブレター": "carb",
    "キャブ": "carb",
    "ピストンリング": "ring",
    "ピストン": "piston",
    "リング": "ring",
    "シリンダーケース": "cylinder",
    "シリンダー": "cylinder",
    "クランクシャフト": "shaft",
    "シャフト": "shaft",
    "ギアケース": "gear",
    "ギア": "gear",
    "キャリアボディ": "carrier",
    "キャリア": "carrier",
    "プロペラ": "propeller",
    "ペラ": "propeller",
}


class BeforeInfoBoat(TypedDict, total=False):
    boat_number: int
    parts: list[str]
    exhibition_time: Optional[float]
    start_timing_exhibition: Optional[float]
    course_number: Optional[int]
    weight_adjustment: Optional[float]
    tilt_adjustment: Optional[float]


class BeforeInfoPage(TypedDict, total=False):
    boats: list[BeforeInfoBoat]
    stable_plate: Optional[int]
    weather_number: Optional[int]
    wind_speed: Optional[int]
    wind_direction_number: Optional[int]
    wave_height: Optional[int]
    temperature: Optional[float]
    water_temperature: Optional[float]


# ============================================================
# 補助
# ============================================================

_FLOAT_RE = re.compile(r"-?\d+(?:\.\d+)?")
_ST_RE = re.compile(r"([FL]?)\s*\.?\s*(\d+)")


def _strip_xml_prolog(html: str) -> str:
    """<?xml ...?> を除去 (BeautifulSoup の XML 誤判定回避)"""
    return re.sub(r"^\s*<\?xml[^?]*\?>", "", html, count=1).lstrip()


def _to_float(s: Optional[str]) -> Optional[float]:
    if s is None:
        return None
    m = _FLOAT_RE.search(s.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _to_int(s: Optional[str]) -> Optional[int]:
    f = _to_float(s)
    return int(f) if f is not None else None


def _parse_st(s: str) -> Optional[float]:
    """'.18' '0.18' 'F.02' → 0.18 / -0.02 (F フライングは負にする)"""
    s = s.strip().replace("　", "").replace(" ", "")
    if not s or s in ("-", "--"):
        return None
    m = _ST_RE.search(s)
    if not m:
        return None
    flag, digits = m.group(1), m.group(2)
    try:
        val = float("0." + digits) if "." in s or len(digits) <= 2 else float(digits)
    except ValueError:
        return None
    if flag == "F":
        val = -val
    return val


def _extract_parts(text: str) -> list[str]:
    """セル内テキストから part_code リストを抽出"""
    out: list[str] = []
    seen: set[str] = set()
    if not text:
        return out
    norm = text.replace("　", "").replace("\xa0", "")
    keys = sorted(PART_CODE_MAP.keys(), key=len, reverse=True)
    remaining = norm
    for key in keys:
        if key in remaining:
            code = PART_CODE_MAP[key]
            if code not in seen:
                out.append(code)
                seen.add(code)
            remaining = remaining.replace(key, "")
    return out


# ============================================================
# 公開関数
# ============================================================

def parse_beforeinfo(html: str) -> BeforeInfoPage:
    soup = BeautifulSoup(_strip_xml_prolog(html), "lxml")

    page: BeforeInfoPage = {
        "boats": [],
        "stable_plate": 1 if "安定板使用" in html else 0,
        "weather_number": None,
        "wind_speed": None,
        "wind_direction_number": None,
        "wave_height": None,
        "temperature": None,
        "water_temperature": None,
    }

    # ========================================================
    # 1. メインテーブル (table.is-w748) 6艇分
    # ========================================================
    boats_by_no: dict[int, BeforeInfoBoat] = {}
    main_tbl = soup.select_one("table.is-w748")
    if main_tbl is not None:
        for tr in main_tbl.select("tbody tr"):
            cells = tr.find_all("td")
            if not cells:
                continue
            first = cells[0].get_text(strip=True)
            if first not in {"1", "2", "3", "4", "5", "6"}:
                continue
            boat_no = int(first)
            # 列インデックス（DOM 観察結果）:
            #   0=枠, 1=写真, 2=選手名, 3=体重, 4=展示タイム, 5=チルト, 6=プロペラ, 7=部品交換, 8=前走成績
            def cell_text(idx: int) -> str:
                if idx >= len(cells):
                    return ""
                return cells[idx].get_text(separator=" ", strip=True)

            ex_time = _to_float(cell_text(4))
            tilt = _to_float(cell_text(5))
            parts = _extract_parts(cell_text(7))

            boats_by_no[boat_no] = BeforeInfoBoat(
                boat_number=boat_no,
                parts=parts,
                exhibition_time=ex_time,
                tilt_adjustment=tilt,
                weight_adjustment=None,
                course_number=None,
                start_timing_exhibition=None,
            )

    # ========================================================
    # 2. スタート展示テーブル (table.is-w238)
    #   tr の各セル中身が "<艇番号> <ST>" 形式 (例: '1 .18', '5 F.02')
    #   tr インデックスがコース番号 (1〜6)
    # ========================================================
    start_tbl = soup.select_one("table.is-w238")
    if start_tbl is not None:
        course_idx = 0
        for tr in start_tbl.select("tbody tr"):
            tds = tr.find_all(["td", "th"])
            txt = " ".join(td.get_text(separator=" ", strip=True) for td in tds)
            if not txt:
                continue
            # 艇番号と ST を抽出
            m = re.match(r"\s*([1-6])\s+([FL]?\.\s*\d+)", txt)
            if not m:
                continue
            course_idx += 1
            try:
                bn = int(m.group(1))
            except ValueError:
                continue
            st = _parse_st(m.group(2))
            if bn in boats_by_no:
                boats_by_no[bn]["start_timing_exhibition"] = st
                boats_by_no[bn]["course_number"] = course_idx

    page["boats"] = sorted(boats_by_no.values(), key=lambda b: b["boat_number"])

    # ========================================================
    # 3. 天候パネル (div.weather1)
    # ========================================================
    weather_panel = soup.select_one("div.weather1")
    if weather_panel:
        ptxt = weather_panel.get_text(separator=" ")
        m = re.search(r"気温\s*([-\d.]+)", ptxt)
        if m:
            page["temperature"] = _to_float(m.group(1))
        m = re.search(r"水温\s*([-\d.]+)", ptxt)
        if m:
            page["water_temperature"] = _to_float(m.group(1))
        m = re.search(r"風速\s*([\d.]+)", ptxt)
        if m:
            page["wind_speed"] = _to_int(m.group(1))
        m = re.search(r"波高?\s*([\d.]+)", ptxt)
        if m:
            page["wave_height"] = _to_int(m.group(1))
        # 天候・風向は <p class="weather1_bodyUnitImage is-weather{1-5}"> /
        # is-wind{1-17}> の CSS クラスから抽出。
        # 1=晴 / 2=曇 / 3=雨 / 4=霧 / 5=雪 / 風向 1-16=16方位, 17=無風
        # (この抽出を入れないと scrape_beforeinfo_live が weather_number を
        #  常に None で返し COALESCE で朝の Open API 値が消えず雨除外が
        #  誤って残り続ける。2026-05-28 浜名湖12R で発覚した実バグ修正。)
        for img in weather_panel.select("p.weather1_bodyUnitImage"):
            cls_attr = " ".join(img.get("class", []))
            m = re.search(r"is-weather(\d+)\b", cls_attr)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 5:
                    page["weather_number"] = n
            m = re.search(r"is-wind(\d+)\b", cls_attr)
            if m:
                n = int(m.group(1))
                if 1 <= n <= 17:
                    page["wind_direction_number"] = n

    return page
