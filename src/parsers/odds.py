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


def parse_exacta_odds(html: str) -> dict[str, float]:
    """二連単オッズ (odds2tf ページ) を {'1-2': 6.4, ...} (30通り) にする。

    ページには「2連単オッズ」と「2連複オッズ」の2表が同じ形で並ぶ。
    どちらもヘッダ行に 1〜6 の艇番と選手名が交互に並び、その下に
    [2着艇, オッズ] の組が 1着艇ごとのブロック (2列) で 5 行入る。
    二連複の表は左上が三角形に空くので「空セルが無い方」を二連単とみなす。
    """
    soup = BeautifulSoup(_strip_xml_prolog(html), "lxml")
    odds_map: dict[str, float] = {}

    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")
        if len(rows) < 6:
            continue
        head_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        digits = [c for c in head_cells if c in {"1", "2", "3", "4", "5", "6"}]
        if len(set(digits)) != 6:
            continue
        grid = _expand_rowspan(tbl)
        data_rows = grid[1:6]
        if len(data_rows) < 5:
            continue
        found: dict[str, float] = {}
        for block in range(6):
            first_no = block + 1
            col_off = block * 2
            for row in data_rows:
                if len(row) < col_off + 2:
                    continue
                try:
                    second_no = int(row[col_off].strip())
                except (ValueError, AttributeError):
                    continue
                if not (1 <= second_no <= 6) or second_no == first_no:
                    continue
                o = _to_odds(row[col_off + 1])
                if o is None:
                    continue
                found[f"{first_no}-{second_no}"] = o
        # 二連複は 15 通りしか埋まらない。二連単は 30 通りそろう。
        if len(found) > len(odds_map):
            odds_map = found
        if len(odds_map) == 30:
            break

    return odds_map


def _six_boat_header_tables(soup):
    """ヘッダ行に 1〜6 の艇番がそろう表だけを返す (オッズ表の共通の目印)。"""
    for tbl in soup.find_all("table"):
        rows = tbl.find_all("tr")
        if len(rows) < 3:
            continue
        head_cells = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        if len({c for c in head_cells if c in {"1", "2", "3", "4", "5", "6"}}) == 6:
            yield tbl


def parse_quinella_odds(html: str) -> dict[str, float]:
    """二連複オッズ (odds2tf ページの 2 つ目の表) を {'1-2': 3.1, ...} (最大15通り) にする。

    二連単と同じページ・同じ形 (1着艇ごとに [相手艇, オッズ] の 2 列) だが、
    左上から三角形に埋まり「小さい艇番-大きい艇番」の組しか並ばない。
    二連単の表は 1-2 と 2-1 の両方を持つので、「昇順の組しか出てこない表」を
    二連複とみなす。欠場などで数字が欠けた組は読み飛ばし、取れた分だけ返す
    (15 通りそろったかは呼び出し側が数えて記録する)。
    """
    soup = BeautifulSoup(_strip_xml_prolog(html), "lxml")
    for tbl in _six_boat_header_tables(soup):
        grid = _expand_rowspan(tbl)
        # 二連単・二連複の表は データ 5 行 × 12 列 (6 艇 × [相手, オッズ])。
        # 三連複 (10 行 × 18 列) や三連単 (20 行 × 18 列) を 2 列ずつ読むと
        # 偶然「昇順の組」に見える値を拾うので、形で先に弾く。
        if len(grid) != 6 or max((len(r) for r in grid[1:]), default=0) > 12:
            continue
        found: dict[str, float] = {}
        ascending_only = True
        for row in grid[1:6]:
            for block in range(6):
                first_no = block + 1
                col_off = block * 2
                if len(row) < col_off + 2:
                    continue
                try:
                    second_no = int(row[col_off].strip())
                except (ValueError, AttributeError):
                    continue
                if not (1 <= second_no <= 6) or second_no == first_no:
                    continue
                o = _to_odds(row[col_off + 1])
                if o is None:
                    continue
                if second_no < first_no:
                    ascending_only = False
                found[f"{first_no}-{second_no}"] = o
        if found and ascending_only:
            return found
    return {}


def parse_trio_odds(html: str) -> dict[str, float]:
    """三連複オッズ (odds3f ページ) を {'1-2-3': 4.2, ...} (最大20通り) にする。

    三連単と同じく 1 着艇ごとの 3 列ブロック [2番目, 3番目, オッズ] だが、
    組は昇順 (1<2<3) だけ・左上から三角形に埋まり、2番目の艇は rowspan で
    縦に続く。空セルや数字でない組 (欠場など) は読み飛ばし、取れた分だけ返す。
    """
    soup = BeautifulSoup(_strip_xml_prolog(html), "lxml")
    best: dict[str, float] = {}
    for tbl in _six_boat_header_tables(soup):
        grid = _expand_rowspan(tbl)
        # 三連複の表は データ 10 行 (三連単は 20 行)。三連単の表から昇順の組だけ
        # 拾ってしまわないよう、形で先に弾く。
        if len(grid) != 11:
            continue
        found: dict[str, float] = {}
        for row in grid[1:]:
            for block in range(6):
                first_no = block + 1
                col_off = block * 3
                if len(row) < col_off + 3:
                    continue
                try:
                    second_no = int(row[col_off].strip())
                    third_no = int(row[col_off + 1].strip())
                except (ValueError, AttributeError):
                    continue
                if not (first_no < second_no < third_no <= 6):
                    continue
                o = _to_odds(row[col_off + 2])
                if o is None:
                    continue
                found[f"{first_no}-{second_no}-{third_no}"] = o
        if len(found) > len(best):
            best = found
    return best
