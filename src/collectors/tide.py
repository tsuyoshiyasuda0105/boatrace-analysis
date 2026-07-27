from __future__ import annotations

import json
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import config
from src.db.connection import connect as db_connect

JST = timezone(timedelta(hours=9))
HIGH_LOW_ZONE_MINUTES = 90


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def _to_float(value) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _to_int(value) -> Optional[int]:
    if value in (None, "", "-"):
        return None
    try:
        return int(value)
    except Exception:
        return None


def _chunk(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)]


def _parse_jma_2digit(text: str) -> Optional[int]:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except Exception:
        return None


def _parse_jma_hhmm(text: str, base_date: datetime) -> Optional[datetime]:
    raw = (text or "").strip()
    if not raw or raw == "9999":
        return None
    try:
        hh = int(raw[:2])
        mm = int(raw[2:4])
        return base_date.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except Exception:
        return None


def load_tide_station_map(path: Optional[Path] = None) -> dict:
    path = path or (config.MASTER_DIR / "tide_stations.json")
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def mapped_stadium_numbers_for_station(station_name: str, mapping: Optional[dict] = None) -> list[int]:
    mapping = mapping or load_tide_station_map()
    wanted = station_name.strip()
    out: list[int] = []
    for key, info in mapping.items():
        if not str(key).isdigit():
            continue
        primary = str(info.get("primary_station", "")).strip()
        primary_code = str(info.get("primary_station_code", "")).strip().upper()
        candidates = [str(x.get("name", "")).strip() for x in info.get("station_candidates", [])]
        candidate_codes = [str(x.get("code", "")).strip().upper() for x in info.get("station_candidates", [])]
        wanted_code = wanted.upper()
        if wanted and (
            wanted == primary
            or wanted in candidates
            or (wanted_code and (wanted_code == primary_code or wanted_code in candidate_codes))
        ):
            out.append(int(key))
    return sorted(set(out))


def _station_info_for_stadium(stadium_number: int, mapping: Optional[dict] = None) -> Optional[dict]:
    mapping = mapping or load_tide_station_map()
    info = mapping.get(str(int(stadium_number)))
    if not info:
        return None
    codes: list[str] = []
    primary_code = str(info.get("primary_station_code", "")).strip().upper()
    if primary_code:
        codes.append(primary_code)
    for cand in info.get("station_candidates", []):
        cand_code = str(cand.get("code", "")).strip().upper()
        if cand_code and cand_code not in codes:
            codes.append(cand_code)
    if not codes:
        return None
    return {
        "station": str(info.get("primary_station", "")).strip() or codes[0],
        "codes": codes,
    }


def _fetch_station_text(codes: list[str], year: int, timeout: int = 15) -> tuple[str, str]:
    last_error = None
    for code in codes:
        url = f"https://www.data.jma.go.jp/kaiyou/data/db/tide/suisan/txt/{year}/{code}.txt"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as res:
                return code, res.read().decode("utf-8")
        except Exception as exc:
            last_error = exc
    raise last_error or RuntimeError("station fetch failed")


def ensure_race_tides_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS race_tides (
          race_id               TEXT PRIMARY KEY,
          stadium_number        INTEGER NOT NULL,
          tide_station          TEXT NOT NULL,
          race_time             TEXT NOT NULL,
          tide_height_cm        REAL,
          tide_phase            TEXT,
          nearest_high_time     TEXT,
          nearest_high_cm       REAL,
          nearest_low_time      TEXT,
          nearest_low_cm        REAL,
          minutes_from_high     INTEGER,
          minutes_from_low      INTEGER,
          tide_range_cm         REAL,
          tide_delta_60m_cm     REAL,
          is_high_tide_zone     INTEGER NOT NULL DEFAULT 0,
          is_low_tide_zone      INTEGER NOT NULL DEFAULT 0,
          source                TEXT,
          fetched_at            TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_race_tides_stadium_time ON race_tides(stadium_number, race_time)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_race_tides_phase ON race_tides(tide_phase, is_high_tide_zone, is_low_tide_zone)")


def _sort_points(points: Iterable[dict]) -> list[dict]:
    rows = []
    for row in points:
        dt = _parse_dt(row.get("time"))
        h = _to_float(row.get("height_cm"))
        if not dt or h is None:
            continue
        rows.append({"time": dt, "height_cm": h})
    return sorted(rows, key=lambda x: x["time"])


def _sort_extremes(extremes: Iterable[dict]) -> list[dict]:
    rows = []
    for row in extremes:
        dt = _parse_dt(row.get("time"))
        kind = str(row.get("kind", "")).strip().lower()
        h = _to_float(row.get("height_cm"))
        if not dt or kind not in {"high", "low"}:
            continue
        rows.append({"time": dt, "kind": kind, "height_cm": h})
    return sorted(rows, key=lambda x: x["time"])


def _nearest_point(points: list[dict], target: datetime) -> Optional[dict]:
    if not points:
        return None
    return min(points, key=lambda x: abs((x["time"] - target).total_seconds()))


def _point_at_or_after(points: list[dict], target: datetime) -> Optional[dict]:
    for row in points:
        if row["time"] >= target:
            return row
    return points[-1] if points else None


def _prev_next_extremes(extremes: list[dict], target: datetime) -> tuple[Optional[dict], Optional[dict]]:
    prev_row = None
    next_row = None
    for row in extremes:
        if row["time"] <= target:
            prev_row = row
        elif row["time"] > target and next_row is None:
            next_row = row
            break
    return prev_row, next_row


def _nearest_kind(extremes: list[dict], target: datetime, kind: str) -> Optional[dict]:
    rows = [row for row in extremes if row["kind"] == kind]
    if not rows:
        return None
    return min(rows, key=lambda x: abs((x["time"] - target).total_seconds()))


def _minutes_from(target: datetime, row: Optional[dict]) -> Optional[int]:
    if not row:
        return None
    return int(round((target - row["time"]).total_seconds() / 60.0))


def _classify_phase(target: datetime, extremes: list[dict]) -> str:
    prev_row, next_row = _prev_next_extremes(extremes, target)
    nearest_high = _nearest_kind(extremes, target, "high")
    nearest_low = _nearest_kind(extremes, target, "low")

    if nearest_high and abs((target - nearest_high["time"]).total_seconds()) <= HIGH_LOW_ZONE_MINUTES * 60:
        return "high"
    if nearest_low and abs((target - nearest_low["time"]).total_seconds()) <= HIGH_LOW_ZONE_MINUTES * 60:
        return "low"

    if prev_row and next_row:
        if prev_row["kind"] == "low" and next_row["kind"] == "high":
            return "rising"
        if prev_row["kind"] == "high" and next_row["kind"] == "low":
            return "falling"
    return "unknown"


def build_race_tide_row(
    race_id: str,
    stadium_number: int,
    race_time: str,
    tide_station: str,
    points: list[dict],
    extremes: list[dict],
    source: str,
    fetched_at: Optional[str] = None,
) -> Optional[dict]:
    target = _parse_dt(race_time)
    if not target:
        return None

    nearest_point = _nearest_point(points, target)
    plus_60 = _point_at_or_after(points, target + timedelta(minutes=60))
    nearest_high = _nearest_kind(extremes, target, "high")
    nearest_low = _nearest_kind(extremes, target, "low")
    phase = _classify_phase(target, extremes)

    current_height = nearest_point["height_cm"] if nearest_point else None
    delta_60 = None
    if current_height is not None and plus_60 is not None:
        delta_60 = plus_60["height_cm"] - current_height

    high_cm = nearest_high["height_cm"] if nearest_high else None
    low_cm = nearest_low["height_cm"] if nearest_low else None
    tide_range = None
    if high_cm is not None and low_cm is not None:
        tide_range = high_cm - low_cm

    minutes_from_high = _minutes_from(target, nearest_high)
    minutes_from_low = _minutes_from(target, nearest_low)

    return {
        "race_id": race_id,
        "stadium_number": stadium_number,
        "tide_station": tide_station,
        "race_time": _iso(target),
        "tide_height_cm": current_height,
        "tide_phase": phase,
        "nearest_high_time": _iso(nearest_high["time"]) if nearest_high else None,
        "nearest_high_cm": high_cm,
        "nearest_low_time": _iso(nearest_low["time"]) if nearest_low else None,
        "nearest_low_cm": low_cm,
        "minutes_from_high": minutes_from_high,
        "minutes_from_low": minutes_from_low,
        "tide_range_cm": tide_range,
        "tide_delta_60m_cm": delta_60,
        "is_high_tide_zone": 1 if minutes_from_high is not None and abs(minutes_from_high) <= HIGH_LOW_ZONE_MINUTES else 0,
        "is_low_tide_zone": 1 if minutes_from_low is not None and abs(minutes_from_low) <= HIGH_LOW_ZONE_MINUTES else 0,
        "source": source,
        "fetched_at": fetched_at or datetime.now(JST).isoformat(),
    }


def upsert_race_tides(conn: sqlite3.Connection, rows: Iterable[dict]) -> int:
    count = 0
    for row in rows:
        conn.execute(
            """
            INSERT INTO race_tides (
              race_id, stadium_number, tide_station, race_time,
              tide_height_cm, tide_phase,
              nearest_high_time, nearest_high_cm,
              nearest_low_time, nearest_low_cm,
              minutes_from_high, minutes_from_low,
              tide_range_cm, tide_delta_60m_cm,
              is_high_tide_zone, is_low_tide_zone,
              source, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (race_id) DO UPDATE SET
              stadium_number = excluded.stadium_number,
              tide_station = excluded.tide_station,
              race_time = excluded.race_time,
              tide_height_cm = excluded.tide_height_cm,
              tide_phase = excluded.tide_phase,
              nearest_high_time = excluded.nearest_high_time,
              nearest_high_cm = excluded.nearest_high_cm,
              nearest_low_time = excluded.nearest_low_time,
              nearest_low_cm = excluded.nearest_low_cm,
              minutes_from_high = excluded.minutes_from_high,
              minutes_from_low = excluded.minutes_from_low,
              tide_range_cm = excluded.tide_range_cm,
              tide_delta_60m_cm = excluded.tide_delta_60m_cm,
              is_high_tide_zone = excluded.is_high_tide_zone,
              is_low_tide_zone = excluded.is_low_tide_zone,
              source = excluded.source,
              fetched_at = excluded.fetched_at
            """,
            (
                row["race_id"], row["stadium_number"], row["tide_station"], row["race_time"],
                row.get("tide_height_cm"), row.get("tide_phase"),
                row.get("nearest_high_time"), row.get("nearest_high_cm"),
                row.get("nearest_low_time"), row.get("nearest_low_cm"),
                row.get("minutes_from_high"), row.get("minutes_from_low"),
                row.get("tide_range_cm"), row.get("tide_delta_60m_cm"),
                row.get("is_high_tide_zone", 0), row.get("is_low_tide_zone", 0),
                row.get("source"), row.get("fetched_at"),
            ),
        )
        count += 1
    return count


def _load_payload(json_path: Path) -> dict:
    return json.loads(json_path.read_text(encoding="utf-8-sig"))


def parse_jma_tide_text(
    text: str,
    station_name: Optional[str] = None,
    source: str = "jma_tide_txt",
    fetched_at: Optional[str] = None,
) -> dict:
    points: list[dict] = []
    extremes: list[dict] = []
    station_code: Optional[str] = None

    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        if len(raw_line) < 136:
            continue

        hourly_raw = raw_line[:72]
        ymd_raw = raw_line[72:78]
        code_raw = raw_line[78:80]
        highs_raw = raw_line[80:108]
        lows_raw = raw_line[108:136]

        yy = _parse_jma_2digit(ymd_raw[:2])
        mm = _parse_jma_2digit(ymd_raw[2:4])
        dd = _parse_jma_2digit(ymd_raw[4:6])
        if yy is None or mm is None or dd is None:
            continue

        base_date = datetime(2000 + yy, mm, dd, tzinfo=JST)
        station_code = (code_raw or "").strip() or station_code

        for hour, chunk in enumerate(_chunk(hourly_raw, 3)):
            height = _to_int(chunk)
            if height is None:
                continue
            dt = base_date.replace(hour=hour, minute=0, second=0, microsecond=0)
            points.append({"time": _iso(dt), "height_cm": height})

        for chunk in _chunk(highs_raw, 7):
            if len(chunk) < 7:
                continue
            dt = _parse_jma_hhmm(chunk[:4], base_date)
            height = _to_int(chunk[4:7])
            if dt is None or height is None or height == 999:
                continue
            extremes.append({"kind": "high", "time": _iso(dt), "height_cm": height})

        for chunk in _chunk(lows_raw, 7):
            if len(chunk) < 7:
                continue
            dt = _parse_jma_hhmm(chunk[:4], base_date)
            height = _to_int(chunk[4:7])
            if dt is None or height is None or height == 999:
                continue
            extremes.append({"kind": "low", "time": _iso(dt), "height_cm": height})

    payload = {
        "station": station_name or station_code,
        "station_code": station_code,
        "source": source,
        "fetched_at": fetched_at or datetime.now(JST).isoformat(),
        "points": points,
        "extremes": extremes,
    }
    return payload


def parse_jma_tide_txt_file(
    txt_path: str | Path,
    station_name: Optional[str] = None,
    source: str = "jma_tide_txt",
    fetched_at: Optional[str] = None,
) -> dict:
    path = Path(txt_path)
    text = path.read_text(encoding="utf-8-sig")
    return parse_jma_tide_text(
        text,
        station_name=station_name,
        source=source,
        fetched_at=fetched_at,
    )


def import_race_rows_payload(payload: dict, db_path: Optional[str] = None) -> dict:
    conn = db_connect(db_path)
    ensure_race_tides_table(conn)
    try:
        rows = payload.get("race_rows", [])
        normalized = []
        for row in rows:
            race_time = row.get("race_time")
            target = _parse_dt(race_time)
            if not target:
                continue
            normalized.append({
                "race_id": row["race_id"],
                "stadium_number": int(row["stadium_number"]),
                "tide_station": row["tide_station"],
                "race_time": _iso(target),
                "tide_height_cm": _to_float(row.get("tide_height_cm")),
                "tide_phase": row.get("tide_phase"),
                "nearest_high_time": _iso(_parse_dt(row.get("nearest_high_time"))),
                "nearest_high_cm": _to_float(row.get("nearest_high_cm")),
                "nearest_low_time": _iso(_parse_dt(row.get("nearest_low_time"))),
                "nearest_low_cm": _to_float(row.get("nearest_low_cm")),
                "minutes_from_high": _to_int(row.get("minutes_from_high")),
                "minutes_from_low": _to_int(row.get("minutes_from_low")),
                "tide_range_cm": _to_float(row.get("tide_range_cm")),
                "tide_delta_60m_cm": _to_float(row.get("tide_delta_60m_cm")),
                "is_high_tide_zone": int(row.get("is_high_tide_zone", 0) or 0),
                "is_low_tide_zone": int(row.get("is_low_tide_zone", 0) or 0),
                "source": row.get("source") or payload.get("source") or "external",
                "fetched_at": _iso(_parse_dt(row.get("fetched_at"))) or datetime.now(JST).isoformat(),
            })
        n = upsert_race_tides(conn, normalized)
        conn.commit()
        return {"mode": "race_rows", "rows": n}
    finally:
        conn.close()


def import_station_payload(
    payload: dict,
    db_path: Optional[str] = None,
    station_name: Optional[str] = None,
    stadium_numbers: Optional[list[int]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    only_missing: bool = False,
) -> dict:
    conn = db_connect(db_path)
    ensure_race_tides_table(conn)
    mapping = load_tide_station_map()
    try:
        station = station_name or payload.get("station") or payload.get("station_name") or payload.get("station_code")
        if not station:
            raise ValueError("station name is required in payload or argument")
        if not stadium_numbers:
            stadium_numbers = mapped_stadium_numbers_for_station(station, mapping)
        if not stadium_numbers:
            raise ValueError(f"no mapped stadiums for station: {station}")

        points = _sort_points(payload.get("points", []))
        extremes = _sort_extremes(payload.get("extremes", []))
        source = payload.get("source") or "external"
        fetched_at = payload.get("fetched_at") or datetime.now(JST).isoformat()

        placeholders = ",".join("?" for _ in stadium_numbers)
        sql = f"""
            SELECT race_id, stadium_number, race_closed_at, race_date
              FROM races
             WHERE stadium_number IN ({placeholders})
        """
        params: list = list(stadium_numbers)
        if date_from:
            sql += " AND race_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND race_date <= ?"
            params.append(date_to)
        if only_missing:
            sql += " AND race_id NOT IN (SELECT race_id FROM race_tides)"
        sql += " ORDER BY race_date, stadium_number, race_number"

        races = conn.execute(sql, params).fetchall()
        rows = []
        for race_id, stadium_number, race_closed_at, race_date in races:
            if not race_closed_at:
                continue
            built = build_race_tide_row(
                race_id=race_id,
                stadium_number=stadium_number,
                race_time=race_closed_at,
                tide_station=station,
                points=points,
                extremes=extremes,
                source=source,
                fetched_at=fetched_at,
            )
            if built:
                rows.append(built)

        n = upsert_race_tides(conn, rows)
        conn.commit()
        return {
            "mode": "station_payload",
            "station": station,
            "stadiums": stadium_numbers,
            "races_scanned": len(races),
            "rows": n,
        }
    finally:
        conn.close()


def refresh_tides_for_races(
    race_ids: Iterable[str],
    db_path: Optional[str] = None,
    timeout: int = 15,
) -> dict:
    target_ids = [str(x).strip() for x in race_ids if str(x).strip()]
    if not target_ids:
        return {"mode": "refresh_tides_for_races", "target_races": 0, "rows": 0, "stations": 0}

    mapping = load_tide_station_map()
    placeholders = ",".join("?" for _ in target_ids)
    conn = db_connect(db_path)
    ensure_race_tides_table(conn)
    try:
        races = conn.execute(
            f"""
            SELECT race_id, stadium_number, race_closed_at
              FROM races
             WHERE race_id IN ({placeholders})
            """,
            target_ids,
        ).fetchall()
        if not races:
            return {"mode": "refresh_tides_for_races", "target_races": 0, "rows": 0, "stations": 0}

        grouped: dict[tuple[str, int], dict] = {}
        for race_id, stadium_number, race_closed_at in races:
            if not race_closed_at:
                continue
            station_info = _station_info_for_stadium(int(stadium_number), mapping)
            if not station_info:
                continue
            year = _parse_dt(race_closed_at).year if _parse_dt(race_closed_at) else datetime.now(JST).year
            key = (station_info["codes"][0], year)
            grouped.setdefault(
                key,
                {
                    "codes": station_info["codes"],
                    "year": year,
                    "races": [],
                },
            )["races"].append((race_id, int(stadium_number), race_closed_at))

        rows: list[dict] = []
        station_fetches = 0
        station_failures = 0
        failed_codes: list[str] = []
        for (_primary_code, year), info in grouped.items():
            try:
                used_code, text = _fetch_station_text(info["codes"], year, timeout=timeout)
            except Exception:
                station_failures += 1
                failed_codes.append(str(info["codes"][0]))
                continue
            station_fetches += 1
            payload = parse_jma_tide_text(
                text,
                station_name=used_code,
                source=f"jma_tide_txt:{used_code}:{year}",
            )
            points = _sort_points(payload.get("points", []))
            extremes = _sort_extremes(payload.get("extremes", []))
            fetched_at = payload.get("fetched_at") or datetime.now(JST).isoformat()
            for race_id, stadium_number, race_closed_at in info["races"]:
                built = build_race_tide_row(
                    race_id=race_id,
                    stadium_number=stadium_number,
                    race_time=race_closed_at,
                    tide_station=used_code,
                    points=points,
                    extremes=extremes,
                    source=f"jma_tide_txt:{used_code}:{year}",
                    fetched_at=fetched_at,
                )
                if built:
                    rows.append(built)

        n = upsert_race_tides(conn, rows)
        conn.commit()
        return {
            "mode": "refresh_tides_for_races",
            "target_races": len(races),
            "rows": n,
            "stations": station_fetches,
            "station_failures": station_failures,
            "failed_codes": failed_codes,
        }
    finally:
        conn.close()


def import_tide_json(
    json_path: str,
    db_path: Optional[str] = None,
    station_name: Optional[str] = None,
    stadium_numbers: Optional[list[int]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    only_missing: bool = False,
) -> dict:
    payload = _load_payload(Path(json_path))
    if payload.get("race_rows"):
        return import_race_rows_payload(payload, db_path=db_path)
    return import_station_payload(
        payload,
        db_path=db_path,
        station_name=station_name,
        stadium_numbers=stadium_numbers,
        date_from=date_from,
        date_to=date_to,
        only_missing=only_missing,
    )


def import_jma_tide_txt(
    txt_path: str,
    db_path: Optional[str] = None,
    station_name: Optional[str] = None,
    stadium_numbers: Optional[list[int]] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    only_missing: bool = False,
    source: str = "jma_tide_txt",
) -> dict:
    payload = parse_jma_tide_txt_file(
        txt_path,
        station_name=station_name,
        source=source,
    )
    return import_station_payload(
        payload,
        db_path=db_path,
        station_name=station_name,
        stadium_numbers=stadium_numbers,
        date_from=date_from,
        date_to=date_to,
        only_missing=only_missing,
    )
