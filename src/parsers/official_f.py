"""Layer 1: ファン手帳 (F file) パーサ

公式ダウンロード:
  https://www.boatrace.jp/static_extra/pc_static/download/data/kibetsu/fan{YYMM}.lzh
  半期更新 (4月: 前期, 10月: 後期)

ファイル形式:
  - Shift_JIS (cp932) 固定幅テキスト
  - 1 行 = 1 レーサー = 403 バイト (CRLF 含めず)
  - 約 1,640 名分

フィールド (0-indexed byte offset):
  [0-3]    4   登録番号 (toban)
  [4-19]   16  氏名漢字 (SJIS 全角 4 文字、全角スペース埋め)
  [20-34]  15  氏名カナ (半角カナ 8 文字、空白埋め)
  [35-38]  4   支部 (SJIS 全角 2 文字)
  [39-40]  2   級別 ("A1"/"A2"/"B1"/"B2")
  [41]     1   生年元号 ("S"=昭和 "H"=平成 "R"=令和)
  [42-47]  6   生年月日 (YYMMDD)
  [48]     1   性別 ('1'=男 '2'=女)
  [49-]    -   身長・体重・血液型・勝率等 (後続)

NOTE: 当面は基本フィールドのみ実装。期別成績は racer_period_stats で別ファイル扱い。
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

# 元号 → 西暦オフセット
ERA_OFFSET = {
    "S": 1925,  # 昭和元年 = 1926年
    "H": 1988,  # 平成元年 = 1989年
    "R": 2018,  # 令和元年 = 2019年
}


def _decode_jp(b: bytes) -> str:
    """SJIS バイト列を全角スペース除去で str に。"""
    try:
        s = b.decode("cp932", errors="replace")
    except Exception:
        return ""
    # 全角スペース・半角スペース両方を除去
    return s.replace("　", "").strip()


def _parse_birth_date(era_byte: int, ymd: bytes) -> Optional[str]:
    """元号 1 byte + YYMMDD 6 byte → ISO date string."""
    try:
        era_char = chr(era_byte)
        if era_char not in ERA_OFFSET:
            return None
        ymd_str = ymd.decode("ascii", errors="replace")
        yy = int(ymd_str[0:2])
        mm = int(ymd_str[2:4])
        dd = int(ymd_str[4:6])
        if mm == 0 or dd == 0:
            return None
        year = ERA_OFFSET[era_char] + yy
        return date(year, mm, dd).isoformat()
    except (ValueError, IndexError):
        return None


def parse_fan_line(line: bytes) -> Optional[dict]:
    """ファン手帳 1 レーサー行をパース。"""
    if len(line) < 49:
        return None
    try:
        toban_str = line[0:4].decode("ascii", errors="replace").strip()
        if not toban_str.isdigit():
            return None
        toban = int(toban_str)
    except Exception:
        return None

    name = _decode_jp(line[4:20])
    name_kana = _decode_jp(line[20:35])
    branch = _decode_jp(line[35:39])
    class_str = line[39:41].decode("ascii", errors="replace").strip()
    class_map = {"A1": 1, "A2": 2, "B1": 3, "B2": 4}
    class_number = class_map.get(class_str)

    birth_date = _parse_birth_date(line[41], line[42:48])

    gender_byte = line[48]
    if gender_byte == ord("1"):
        gender = 1  # 男
    elif gender_byte == ord("2"):
        gender = 2  # 女
    else:
        gender = None

    return {
        "racer_number": toban,
        "name": name,
        "name_kana": name_kana,
        "branch_text": branch,  # 漢字テキスト (将来 branch_number にマップ可)
        "class_number": class_number,
        "birth_date": birth_date,
        "gender": gender,
    }


def parse_fan_file(path: Path) -> list[dict]:
    """ファン手帳 TXT ファイル全体をパース。"""
    raw = path.read_bytes()
    # CRLF / LF 両方対応
    lines = raw.replace(b"\r\n", b"\n").split(b"\n")
    rows: list[dict] = []
    for ln in lines:
        if not ln:
            continue
        row = parse_fan_line(ln)
        if row is None or row["racer_number"] is None:
            continue
        rows.append(row)
    return rows


if __name__ == "__main__":
    # スモークテスト
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m src.parsers.official_f <fan_txt_path>")
        sys.exit(1)
    rows = parse_fan_file(Path(sys.argv[1]))
    print(f"parsed: {len(rows)} racers")
    # 性別分布
    from collections import Counter
    c = Counter(r["gender"] for r in rows)
    print(f"gender: {dict(c)}")
    # 級別分布
    cc = Counter(r["class_number"] for r in rows)
    print(f"class:  {dict(cc)}")
    # サンプル
    print("\nfirst 3 records:")
    for r in rows[:3]:
        print(f"  {r}")
    print("\nlast 3 records:")
    for r in rows[-3:]:
        print(f"  {r}")
