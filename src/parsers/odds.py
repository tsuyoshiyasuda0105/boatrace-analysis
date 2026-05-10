"""
三連単オッズページ HTML パーサー

URL: https://www.boatrace.jp/owpc/pc/race/odds3t?jcd=...&hd=YYYYMMDD&rno=R

DOM 構造 (2026-05 時点):
  メインテーブルは class 無し / 21行
    行0: ヘッダ ([1, '萬...', 2, '田中...', 3, '宮内...', 4, '富永...', 5, '川口...', 6, '澁澤...'])
    行1〜20: データ行。1着艇1〜6 を横6ブロックで並べ、各ブロック内で
              2着艇(rowspan=4)・3着艇・オッズ の順
    各1着ブロックは 5×4=20通り = 5行 (rowspan で2着艇は4行に1回)
    1着艇1〜6 で 6×20=120通り

実装方針:
  pandas.read_html だと rowspan を正しく解釈してくれない場合があるので、
  rowspan を BeautifulSoup で手動展開してから組合せ・オッズを抽出。
"""
from __future__ import annotations

import logging
import re
import warnings
from typing import Optional

from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

logger = logging.getLogger(__name__)


def _strip_xml_prolog(html: str) -> str:
    return re.sub(r"^\s*<\?xml[^?]*\?>", "", html, count=1).lstrip()


def _to_odds(s: str) -> Optional[float]:
    s = s.strip().replace("　", "").replace(",", "")
    if not s or s in ("-", "--", "---", "欠場", "中止", "不出走"):
        return None
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _expand_rowspan(table) -> list[list[str]]:
    """
    rowspan/colspan を考慮してテーブルを2次元配列に展開。
    各セルは get_text(strip=True) の文字列。
    """
    rows = table.find_all("tr")
    grid: list[list[Optional[str]]] = []
    pending: dict[int, tuple[str, int]] = {}  # col_idx -> (text, remaining_rows)

    for tr in rows:
        cells = tr.find_all(["th", "td"])
        out_row: list[str] = []
        col = 0
        ci = 0
        # 先に pending から埋める
        while True:
            # pending に該当列があれば消費
            if col in pending:
                text, rem = pending[col]
                out_row.append(text)
                if rem - 1 <= 0:
                    del pending[col]
                else:
                    pending[col] = (text, rem - 1)
                col += 1
                continue
            if ci >= len(cells):
                break
            c = cells[ci]
            ci += 1
            text = c.get_text(separator=" ", strip=True)
            try:
                rs = int(c.get("rowspan", "1"))
                cs = int(c.get("colspan", "1"))
            except ValueError:
                rs, cs = 1, 1
            for _ in range(cs):
                out_row.append(text)
                if rs > 1:
                    pending[col] = (text, rs - 1)
                col += 1
        # 行末に残った pending も後続に持ち越し（ここでは何もしない）
        grid.append(out_row)

    # pending を後続行に流すため、グリッドの長さ補正
    return grid


def parse_trifecta_odds(html: str) -> dict[str, float]:
    soup = BeautifulSoup(_strip_xml_prolog(html), "lxml")
    odds_map: dict[str, float] = {}

    # 「三連単オッズ」のメインテーブルを特定: 21行 / 1〜6 の選手名がヘッダに並ぶ
    candidates = []
    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")
        if len(rows) < 15:
            continue
        head_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        # 先頭行に '1' '2' '3' '4' '5' '6' が並ぶ (選手名と交互だが少なくとも数字が6つ)
        digits = [c for c in head_cells if c in {"1", "2", "3", "4", "5", "6"}]
        if len(set(digits)) == 6:
            candidates.append(tbl)

    if not candidates:
        return odds_map

    grid = _expand_rowspan(candidates[0])
    if len(grid) < 21:
        return odds_map

    # ヘッダ行を除いた20行 × 18列 (= 6ブロック × 3列)
    # 各ブロック (col 0-2, 3-5, 6-8, ...) は同じ1着艇に対応
    # 各行内で [2着艇, 3着艇, オッズ] が並ぶ
    data_rows = grid[1:]
    if len(data_rows) < 20:
        return odds_map

    for block in range(6):
        first_no = block + 1
        col_off = block * 3
        for row in data_rows[:20]:
            if len(row) < col_off + 3:
                continue
            second_s = row[col_off]
            third_s = row[col_off + 1]
            odds_s = row[col_off + 2]
            try:
                second_no = int(second_s.strip())
                third_no = int(third_s.strip())
            except (ValueError, AttributeError):
                continue
            if not (1 <= second_no <= 6 and 1 <= third_no <= 6):
                continue
            if second_no == first_no or third_no == first_no or second_no == third_no:
                continue
            o = _to_odds(odds_s)
            if o is None:
                continue
            odds_map[f"{first_no}-{second_no}-{third_no}"] = o

    return odds_map
