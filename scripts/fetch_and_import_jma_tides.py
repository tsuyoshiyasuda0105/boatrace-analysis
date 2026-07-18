from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.collectors.tide import (
    import_station_payload,
    load_tide_station_map,
    parse_jma_tide_text,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default=None, help="Override DB path")
    p.add_argument("--stadiums", default=None, help="Comma-separated stadium numbers")
    p.add_argument("--year-from", type=int, required=True)
    p.add_argument("--year-to", type=int, required=True)
    p.add_argument("--only-missing", action="store_true")
    p.add_argument("--timeout", type=int, default=30)
    return p.parse_args()


def wanted_stadiums(args: argparse.Namespace, mapping: dict) -> list[int]:
    if args.stadiums:
        return sorted({int(x.strip()) for x in args.stadiums.split(",") if x.strip()})
    return sorted(int(k) for k in mapping.keys() if str(k).isdigit())


def build_station_groups(mapping: dict, stadiums: list[int]) -> dict[str, dict]:
    groups: dict[str, dict] = defaultdict(lambda: {"station": None, "stadiums": []})
    for stadium in stadiums:
        info = mapping.get(str(stadium))
        if not info:
            continue
        code = str(info.get("primary_station_code", "")).strip().upper()
        station = str(info.get("primary_station", "")).strip()
        if not code:
            continue
        groups[code]["station"] = station or code
        groups[code]["stadiums"].append(stadium)
        groups[code]["codes"] = [code]
        for cand in info.get("station_candidates", []):
            cand_code = str(cand.get("code", "")).strip().upper()
            if cand_code and cand_code not in groups[code]["codes"]:
                groups[code]["codes"].append(cand_code)
    return groups


def fetch_station_text(codes: list[str], year: int, timeout: int) -> tuple[str, str]:
    last_error = None
    for code in codes:
        url = f"https://www.data.jma.go.jp/kaiyou/data/db/tide/suisan/txt/{year}/{code}.txt"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as res:
                return code, res.read().decode("utf-8")
        except Exception as e:
            last_error = e
    raise last_error or RuntimeError("station fetch failed")


def main() -> int:
    args = parse_args()
    mapping = load_tide_station_map()
    if not mapping:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "tide station mapping is empty or missing",
                    "path": str(ROOT_DIR / "master" / "tide_stations.json"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    stadiums = wanted_stadiums(args, mapping)
    groups = build_station_groups(mapping, stadiums)
    if not groups:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": "no tide station groups were built",
                    "stadiums": stadiums,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    summary = []
    for year in range(args.year_from, args.year_to + 1):
        for code, info in groups.items():
            try:
                used_code, text = fetch_station_text(info.get("codes") or [code], year, args.timeout)
            except Exception as e:
                summary.append({
                    "year": year,
                    "station_code": code,
                    "station": info["station"],
                    "stadiums": info["stadiums"],
                    "status": "fetch_error",
                    "error": str(e),
                })
                continue

            payload = parse_jma_tide_text(
                text,
                station_name=used_code,
                source=f"jma_tide_txt:{used_code}:{year}",
            )
            result = import_station_payload(
                payload,
                db_path=args.db,
                station_name=used_code,
                stadium_numbers=sorted(set(info["stadiums"])),
                date_from=f"{year}-01-01",
                date_to=f"{year}-12-31",
                only_missing=args.only_missing,
            )
            summary.append({
                "year": year,
                "station_code": code,
                "used_code": used_code,
                "station": info["station"],
                "stadiums": sorted(set(info["stadiums"])),
                "status": "ok",
                "rows": result.get("rows", 0),
                "races_scanned": result.get("races_scanned", 0),
            })

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
