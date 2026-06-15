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


def parse_original_exhibition(html: str) -> list[OriginalExhibitionRow]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[OriginalExhibitionRow] = []

    for table in soup.find_all("table"):
        header_cells = table.find_all("th")
        if not header_cells:
            continue
        headers = [c.get_text(" ", strip=True) for c in header_cells]
        mapping = {
            idx: field
            for idx, header in enumerate(headers)
            if (field := _classify_header(header))
        }
        fields = set(mapping.values())
        if not ({"lap_time", "turn_time", "straight_time"} & fields):
            continue

        for tr in table.find_all("tr"):
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
