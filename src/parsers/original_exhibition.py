"""Parsers for venue-specific original exhibition tables.

Venue sites do not share a stable format. This parser intentionally accepts
only tables whose headers clearly contain original exhibition fields such as
1周, まわり足, 回り足, or 直線.
"""
from __future__ import annotations

import re
from typing import TypedDict

from bs4 import BeautifulSoup


class OriginalExhibitionRow(TypedDict, total=False):
    boat_number: int
    racer_number: int
    lap_time: float
    turn_time: float
    straight_time: float
    original_rank: int
    raw_text: str


_FLOAT_RE = re.compile(r"\d+(?:\.\d+)?")
_INT_RE = re.compile(r"\d+")


def _to_float(text: str) -> float | None:
    m = _FLOAT_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _to_int(text: str) -> int | None:
    m = _INT_RE.search(text.replace(",", ""))
    if not m:
        return None
    try:
        return int(m.group())
    except ValueError:
        return None


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _classify_header(header: str) -> str | None:
    h = _norm(header)
    if h in {"枠", "枠番", "艇", "艇番"}:
        return "boat_number"
    if "登録" in h:
        return "racer_number"
    if "順位" in h or h == "ランク":
        return "original_rank"
    if "一周" in h:
        return "lap_time"
    if "まわり足" in h or "回り足" in h:
        return "turn_time"
    if "直線" in h or "伸び" in h:
        return "straight_time"
    if "艇番" in h or h in {"艇", "枠", "枠番"}:
        return "boat_number"
    if "登録" in h:
        return "racer_number"
    if "順位" in h or "ランク" in h:
        return "original_rank"
    if "1周" in h or "一周" in h or "周回" in h:
        return "lap_time"
    if "まわり足" in h or "回り足" in h or "回足" in h:
        return "turn_time"
    if "直線" in h or "伸び" in h or "伸足" in h:
        return "straight_time"
    return None


def _extract_boat_number(cells: list[str], mapping: dict[int, str]) -> int | None:
    for idx, field in mapping.items():
        if field == "boat_number":
            n = _to_int(cells[idx])
            if n and 1 <= n <= 6:
                return n
    # Fallback: many venue tables start each row with the boat/frame number.
    for text in cells[:3]:
        n = _to_int(text)
        if n and 1 <= n <= 6:
            return n
    return None


def _table_header_mapping(table) -> dict[int, str]:
    """Expand multi-row/colspan headers into their leaf column positions."""
    header_rows = table.select("thead tr")
    if not header_rows:
        header_rows = [tr for tr in table.find_all("tr") if tr.find("th")]
    if not header_rows:
        return {}

    active_rowspans: dict[int, tuple[int, str]] = {}
    leaf_headers: dict[int, str] = {}
    for tr in header_rows:
        occupied = set(active_rowspans)
        for column, (remaining, text) in list(active_rowspans.items()):
            leaf_headers[column] = text
            if remaining <= 1:
                del active_rowspans[column]
            else:
                active_rowspans[column] = (remaining - 1, text)

        column = 0
        for cell in tr.find_all(["th", "td"], recursive=False):
            while column in occupied:
                column += 1
            text = cell.get_text(" ", strip=True)
            colspan = max(1, int(cell.get("colspan", 1) or 1))
            rowspan = max(1, int(cell.get("rowspan", 1) or 1))
            for offset in range(colspan):
                current = column + offset
                leaf_headers[current] = text
                if rowspan > 1:
                    active_rowspans[current] = (rowspan - 1, text)
            column += colspan

    return {
        idx: field
        for idx, header in leaf_headers.items()
        if (field := _classify_header(header))
    }


def parse_original_exhibition(html: str) -> list[OriginalExhibitionRow]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[OriginalExhibitionRow] = []

    # Kojima's archive stores five numeric controls per boat. The last three
    # are lap, turn and straight time respectively.
    kojima_names = soup.select(".ren-name")
    kojima_controls = soup.select(".control")
    if len(kojima_names) >= 6 and len(kojima_controls) >= 30:
        control_values = [
            _to_float(cell.get_text(" ", strip=True))
            for cell in kojima_controls
        ]
        if len(control_values) >= 30:
            kojima_rows: list[OriginalExhibitionRow] = []
            for boat_number in range(1, 7):
                chunk = control_values[(boat_number - 1) * 5:boat_number * 5]
                if len(chunk) != 5:
                    continue
                row: OriginalExhibitionRow = {
                    "boat_number": boat_number,
                    "raw_text": kojima_names[boat_number - 1].get_text(" ", strip=True),
                }
                for key, value in zip(
                    ("lap_time", "turn_time", "straight_time"),
                    chunk[2:5],
                ):
                    if value is not None:
                        row[key] = value
                if any(key in row for key in ("lap_time", "turn_time", "straight_time")):
                    kojima_rows.append(row)
            if len(kojima_rows) == 6:
                return kojima_rows

    # Tokuyama's mobile archive is a linear document rather than a table.
    page_text = soup.get_text(" ", strip=True)
    tokuyama_values = re.findall(
        r"展示[：:]\s*\d+(?:\.\d+)?\s+一周[：:]\s*(\d+(?:\.\d+)?)"
        r"\s+まわり足[：:]\s*(\d+(?:\.\d+)?)",
        page_text,
    )
    if len(tokuyama_values) == 6:
        return [
            {
                "boat_number": boat_number,
                "lap_time": float(values[0]),
                "turn_time": float(values[1]),
                "raw_text": f"{values[0]} {values[1]}",
            }
            for boat_number, values in enumerate(tokuyama_values, start=1)
        ]

    # Several official venue archives render original exhibition values as
    # parallel CSS columns instead of a conventional table.
    names = soup.select(".com-rname")
    # Fukuoka uses col6 for the normal exhibition time and shifts its
    # original exhibition fields one column to the right.
    if soup.select(".col9"):
        lap_cells = soup.select(".col7")
        turn_cells = soup.select(".col8")
        straight_cells = soup.select(".col9")
    else:
        lap_cells = soup.select(".col6")
        turn_cells = soup.select(".col7")
        straight_cells = soup.select(".col8")
    if len(names) >= 6:
        def numeric(cells) -> list[float]:
            return [
                value
                for cell in cells
                if (value := _to_float(cell.get_text(" ", strip=True))) is not None
            ]

        first_column = numeric(lap_cells)
        # Full lap values are normally 30-40 seconds. Kiryu exposes a roughly
        # 18-second half-lap column here, which must not become lap_time.
        lap_values = (
            first_column[-6:]
            if len(first_column) >= 6 and min(first_column[-6:]) >= 25
            else []
        )
        turn_values = numeric(turn_cells)[-6:]
        straight_values = numeric(straight_cells)[-6:]
        if len(lap_values) == 6 or len(turn_values) == 6 or len(straight_values) == 6:
            css_rows: list[OriginalExhibitionRow] = []
            for boat_number in range(1, 7):
                row: OriginalExhibitionRow = {
                    "boat_number": boat_number,
                    "raw_text": names[boat_number - 1].get_text(" ", strip=True),
                }
                if len(lap_values) == 6:
                    row["lap_time"] = lap_values[boat_number - 1]
                if len(turn_values) == 6:
                    row["turn_time"] = turn_values[boat_number - 1]
                if len(straight_values) == 6:
                    row["straight_time"] = straight_values[boat_number - 1]
                css_rows.append(row)
            return css_rows

    for table in soup.find_all("table"):
        mapping = _table_header_mapping(table)
        header_cells = table.find_all("th")
        first_row = table.find("tr")
        fallback_header_row = False
        if not header_cells and first_row:
            header_cells = first_row.find_all(["th", "td"])
            fallback_header_row = True
        if not header_cells:
            continue
        if not mapping:
            headers = [c.get_text(" ", strip=True) for c in header_cells]
            mapping = {
                idx: field
                for idx, header in enumerate(headers)
                if (field := _classify_header(header))
            }
        fields = set(mapping.values())
        if not ({"lap_time", "turn_time", "straight_time"} & fields):
            continue

        table_rows = table.find_all("tr")
        if fallback_header_row:
            table_rows = table_rows[1:]
        for tr in table_rows:
            tds = tr.find_all("td")
            if not tds:
                continue
            cells = [td.get_text(" ", strip=True) for td in tds]
            if len(cells) < 2:
                continue
            boat_number = _extract_boat_number(cells, mapping)
            if not boat_number:
                continue

            row: OriginalExhibitionRow = {
                "boat_number": boat_number,
                "raw_text": " ".join(cells),
            }
            for idx, field in mapping.items():
                if idx >= len(cells):
                    continue
                if field in {"lap_time", "turn_time", "straight_time"}:
                    val = _to_float(cells[idx])
                    if val is not None:
                        row[field] = val
                elif field in {"racer_number", "original_rank"}:
                    val = _to_int(cells[idx])
                    if val is not None:
                        row[field] = val
            if any(k in row for k in ("lap_time", "turn_time", "straight_time")):
                rows.append(row)

    # Deduplicate by boat number, keeping first complete-looking row.
    out: list[OriginalExhibitionRow] = []
    seen: set[int] = set()
    for row in rows:
        bn = row["boat_number"]
        if bn in seen:
            continue
        seen.add(bn)
        out.append(row)
    return out
