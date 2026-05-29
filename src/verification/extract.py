"""自然言語テキストから候補手法を抽出する。

入力: 競艇予想ブログ等の本文テキスト (任意の日本語)
出力: 構造化された method dict のリスト

実装はヒューリスティック (regex + keyword) で、将来 LLM 拡張可能なよう
method 構造を JSON 互換に保つ。

抽出される条件 (実装範囲):
  - stadium       : 会場名 (24 会場のいずれかが文中に出現)
  - course        : "Nコース" / "N号艇" (1-6)
  - racer_class   : A1/A2/B1/B2
  - wind_direction: 追い風 / 向かい風 / 横風
  - wind_speed_min: "風速N m以上"
  - weather_exclude: 雨除外 / 晴限定
  - racer_avg_st_max: "STN以下" / "スタート巧者"
  - odds_min/max  : "N倍-M倍" / "N円台"
  - race_number   : "NR" / "N レース"
  - bet_type      : 単勝 / 2連単 / 3連単 等
  - finish_pattern: "1-2-3" / "頭固定" / "まくり"

抽出された条件が 2 個未満ならノイズとして破棄 (戻り値リストに含めない)。
"""
from __future__ import annotations

import re
from typing import Optional

EXTRACTOR_VERSION = "v0.2"

# B 除外場 (L4 戦略で ROI 低下が認められた場 set)
EXCLUDE_B = {2, 4, 7, 8, 10, 19, 21, 24}
# {戸田, 平和島, 蒲郡, 常滑, 三国, 下関, 芦屋, 大村}

# 決まり手 (race_results.kimarite との照合)
KIMARITE_KEYWORDS = [
    "逃げ", "まくり差し", "差し", "まくり", "抜き", "恵まれ",
]

STADIUMS = {
    "桐生": 1, "戸田": 2, "江戸川": 3, "平和島": 4, "多摩川": 5, "浜名湖": 6,
    "蒲郡": 7, "常滑": 8, "津": 9, "三国": 10, "びわこ": 11, "住之江": 12,
    "尼崎": 13, "鳴門": 14, "丸亀": 15, "児島": 16, "宮島": 17, "徳山": 18,
    "下関": 19, "若松": 20, "芦屋": 21, "福岡": 22, "唐津": 23, "大村": 24,
    "琵琶湖": 11,  # 別表記
    "びわ湖": 11,
}

CLASS_MAP = {"A1": 1, "A2": 2, "B1": 3, "B2": 4}

WIND_KEYWORDS = [
    (re.compile(r"追[いっ]?風|おいかぜ|追風"), "tailwind"),
    (re.compile(r"向か?い風|むかいかぜ|向風"), "headwind"),
    (re.compile(r"横風"), "crosswind"),
]

WEATHER_KEYWORDS = {"晴": 1, "曇": 2, "雨": 3, "霧": 4, "雪": 5}

BET_TYPE_KEYWORDS = [
    ("3連単", "trifecta"), ("三連単", "trifecta"),
    ("2連単", "exacta"), ("二連単", "exacta"),
    ("3連複", "trio"), ("三連複", "trio"),
    ("2連複", "quinella"), ("二連複", "quinella"),
    ("拡連複", "quinella_place"),
    ("単勝", "win"), ("複勝", "place"),
]

# 「ST 早い」「スタート巧者」を意味する語 (具体値が無いとき 0.16 をデフォルトに)
ST_FAST_KEYWORDS = re.compile(
    r"(?:平均\s*)?ST\s*(\d+\.?\d*)\s*(?:以下|未満)"
    r"|スタート\s*(?:が)?\s*早い"
    r"|スタート\s*巧者"
    r"|ST\s*巧者"
)


def extract_methods(text: str, source_url: str = "") -> list[dict]:
    """テキスト全文から候補手法のリストを抽出。段落単位で 1 手法と見なす。"""
    methods = []
    paragraphs = re.split(r"\n\s*\n+", text)
    for para in paragraphs:
        para = para.strip()
        # 日本語は情報密度が高いので短くても可。最終的に extract_single の
        # 「条件 2 個未満なら破棄」がノイズ除去の主役。
        if len(para) < 10:
            continue
        m = extract_single(para, source_url)
        if m is not None:
            methods.append(m)
    return methods


def extract_single(text: str, source_url: str = "") -> Optional[dict]:
    """1 段落から 1 手法 dict を抽出。条件が 2 個未満なら None。"""
    cond: dict = {}

    # 会場
    stadiums = sorted({num for name, num in STADIUMS.items() if name in text})
    if stadiums:
        cond["stadium"] = stadiums

    # クラス
    classes = sorted({num for name, num in CLASS_MAP.items() if name in text})
    if classes:
        cond["racer_class"] = classes

    # 風
    for pat, val in WIND_KEYWORDS:
        if pat.search(text):
            cond["wind_direction"] = val
            break

    # 風速 (M m以上 / M メートル以上)
    m = re.search(r"風速\s*(\d+\.?\d*)\s*(?:m|メートル)\s*以上", text)
    if m:
        try:
            cond["wind_speed_min"] = float(m.group(1))
        except ValueError:
            pass

    # 天候除外 (「雨除外」「晴限定」など)
    for kw, num in WEATHER_KEYWORDS.items():
        if re.search(rf"{kw}\s*(?:を)?\s*(?:除外|除く|避ける|不要|外す)", text):
            cond.setdefault("weather_exclude", []).append(num)

    # コース / 号艇
    # ただし「N号艇 連対率/N着率」のように直後が選手統計キーワードなら
    # course 指定ではなく boat-N の stat 参照と解釈 → 追加しない
    course_set = set()
    for m in re.finditer(r"(?<!\d)([1-6])\s*(?:コース|号艇|カド)", text):
        n = int(m.group(1))
        after = text[m.end():m.end() + 8]
        if re.match(r"\s*(?:連対率|[123]着率|勝率|top|連率)", after):
            continue
        course_set.add(n)
    if course_set:
        cond["course"] = sorted(course_set)

    # ST 早い
    m = ST_FAST_KEYWORDS.search(text)
    if m:
        val = m.group(1)
        if val:
            try:
                cond["racer_avg_st_max"] = float(val)
            except ValueError:
                cond["racer_avg_st_max"] = 0.16
        else:
            cond["racer_avg_st_max"] = 0.16

    # オッズ範囲 (X倍-Y倍 / X倍〜Y倍 / X〜Y倍)
    m = re.search(r"(\d+\.?\d*)\s*[-〜~]\s*(\d+\.?\d*)\s*倍", text)
    if m:
        try:
            cond["odds_min"] = float(m.group(1))
            cond["odds_max"] = float(m.group(2))
        except ValueError:
            pass

    # レース番号 (NR / Nレース)
    rn = set()
    for m in re.finditer(r"(?<!\d)(\d{1,2})\s*(?:R|レース)(?![ル])", text):
        v = int(m.group(1))
        if 1 <= v <= 12:
            rn.add(v)
    if rn:
        cond["race_number"] = sorted(rn)

    # 賭け式
    for kw, bt in BET_TYPE_KEYWORDS:
        if kw in text:
            cond["bet_type"] = bt
            break

    # 着順パターン (3連単 X-Y-Z)
    m = re.search(r"([1-6])\s*[-=]\s*([1-6])\s*[-=]\s*([1-6])", text)
    if m:
        a, b, c = m.group(1), m.group(2), m.group(3)
        if len({a, b, c}) == 3:
            cond["finish_pattern"] = f"{a}-{b}-{c}"
    elif re.search(r"頭固定|1着固定", text):
        cond["finish_pattern"] = "head_fix"

    # 決まり手 (1着の決まり手で検索)
    for kw in KIMARITE_KEYWORDS:
        if kw in text:
            cond["kimarite"] = kw
            break

    # 国1着率 (1号艇): "国1%≥N" / "全国1着率 N% 以上" 等
    m = re.search(r"(?:全国)?1着率\s*[≧≥]?\s*(\d+\.?\d*)\s*%?\s*以上"
                  r"|国1%?\s*[≧≥]\s*(\d+\.?\d*)", text)
    if m:
        try:
            cond["boat1_natl_1_min"] = float(m.group(1) or m.group(2))
        except ValueError:
            pass

    # 地元1着率 (1号艇): "当地1着率" / "ローカル" / "地元勝率"
    m = re.search(r"(?:当地|地元|ローカル|local)\s*1着率\s*[≧≥]?\s*(\d+\.?\d*)\s*%?\s*以上"
                  r"|local_?top_?1\s*[≧≥]\s*(\d+\.?\d*)", text, re.IGNORECASE)
    if m:
        try:
            cond["boat1_local_1_min"] = float(m.group(1) or m.group(2))
        except ValueError:
            pass

    # 地元 2 連率 (top_2)
    m = re.search(r"(?:当地|地元|ローカル)\s*(?:2連率|2着以内率|連対率)\s*[≧≥]?\s*(\d+\.?\d*)\s*%?\s*以上", text)
    if m:
        try:
            cond["boat1_local_2_min"] = float(m.group(1))
        except ValueError:
            pass

    # モーター 2 連率: "モーター2連率 N% 以上" / "良モーター" (デフォ 40)
    m = re.search(r"モーター\s*2連率\s*[≧≥]?\s*(\d+\.?\d*)\s*%?\s*以上", text)
    if m:
        try:
            cond["boat1_motor_top2_min"] = float(m.group(1))
        except ValueError:
            pass
    elif re.search(r"良モーター|モーター.*?(?:絶好|抜群|好調)", text):
        cond["boat1_motor_top2_min"] = 40.0

    # モーター 3 連率
    m = re.search(r"モーター\s*3連率\s*[≧≥]?\s*(\d+\.?\d*)\s*%?\s*以上", text)
    if m:
        try:
            cond["boat1_motor_top3_min"] = float(m.group(1))
        except ValueError:
            pass

    # 2号艇連対率 (top_2)
    m = re.search(r"2号艇\s*(?:連対率|top[_-]?2|2連率)\s*[≧≥]?\s*(\d+\.?\d*)\s*%?\s*以上"
                  r"|2号艇\s*top[_-]?2\s*[≧≥]\s*(\d+\.?\d*)", text)
    if m:
        try:
            cond["boat2_top2_min"] = float(m.group(1) or m.group(2))
        except ValueError:
            pass

    # 3号艇 国1%
    m = re.search(r"3号艇\s*(?:全国)?1着率\s*[≧≥]?\s*(\d+\.?\d*)\s*%?\s*以上"
                  r"|3号艇\s*国1%?\s*[≧≥]\s*(\d+\.?\d*)", text)
    if m:
        try:
            cond["boat3_natl_1_min"] = float(m.group(1) or m.group(2))
        except ValueError:
            pass

    # B 除外場フィルタ
    if re.search(r"B\s*除外|戸田.{0,12}平和島|B\s*級\s*会場\s*除外", text):
        cond["exclude_b_venues"] = True

    # 条件 2 個未満ならノイズ扱い
    if len(cond) < 2:
        return None

    return {
        "extractor_version": EXTRACTOR_VERSION,
        "source_url": source_url,
        "source_quote": text[:300],
        "conditions": cond,
        "confidence": min(1.0, len(cond) / 6.0),
    }


def title_of(method: dict) -> str:
    """method から人間が読みやすいタイトルを生成。"""
    cond = method.get("conditions", {})
    parts: list[str] = []
    rev_stadium = {v: k for k, v in STADIUMS.items() if k not in ("琵琶湖", "びわ湖")}
    rev_class = {v: k for k, v in CLASS_MAP.items()}
    if cond.get("stadium"):
        parts.append("/".join(rev_stadium.get(s, str(s)) for s in cond["stadium"]))
    if cond.get("wind_direction"):
        wind_jp = {"tailwind": "追い風", "headwind": "向かい風", "crosswind": "横風"}
        parts.append(wind_jp.get(cond["wind_direction"], cond["wind_direction"]))
    if cond.get("course"):
        parts.append("/".join(f"{c}コース" for c in cond["course"]))
    if cond.get("racer_class"):
        parts.append("/".join(rev_class.get(c, str(c)) for c in cond["racer_class"]))
    if cond.get("racer_avg_st_max") is not None:
        parts.append(f"ST≤{cond['racer_avg_st_max']:.2f}")
    if cond.get("finish_pattern"):
        parts.append(cond["finish_pattern"])
    return " × ".join(parts) if parts else "(条件未抽出)"
