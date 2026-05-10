"""
Layer 3 パーサー動作確認用スクリプト (1レース分)

使い方:
    python scripts\test_layer3_parser.py --date 20260506 --jcd 1 --rno 1

HTML を data/raw/_test/ に保存してパース結果を表示。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.collectors._http import fetch_html
from src.parsers.beforeinfo import parse_beforeinfo
from src.parsers.odds import parse_trifecta_odds


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="20260506", help="YYYYMMDD")
    p.add_argument("--jcd", type=int, default=1, help="会場番号 1-24")
    p.add_argument("--rno", type=int, default=1, help="レース番号 1-12")
    p.add_argument("--target", choices=["parts", "odds", "both"], default="both")
    args = p.parse_args()

    out_dir = config.RAW_DIR / "_test"
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.target in ("parts", "both"):
        url = config.BEFOREINFO_URL.format(jcd=args.jcd, date=args.date, rno=args.rno)
        print(f"\n=== beforeinfo ===\nURL: {url}")
        html = fetch_html(url)
        if not html:
            print("  FAILED: no HTML returned")
        else:
            (out_dir / f"beforeinfo_{args.date}_{args.jcd:02d}_{args.rno:02d}.html").write_text(
                html, encoding="utf-8"
            )
            print(f"  HTML size: {len(html)} chars")
            result = parse_beforeinfo(html)
            print(f"  weather: {result.get('weather_number')} / "
                  f"wind={result.get('wind_speed')} / "
                  f"wave={result.get('wave_height')} / "
                  f"temp={result.get('temperature')} / water_temp={result.get('water_temperature')}")
            for b in result.get("boats", []):
                print(f"  boat {b.get('boat_number')}: "
                      f"parts={b.get('parts')} "
                      f"ex_time={b.get('exhibition_time')} "
                      f"st_ex={b.get('start_timing_exhibition')} "
                      f"course={b.get('course_number')} "
                      f"tilt={b.get('tilt_adjustment')} "
                      f"weight_adj={b.get('weight_adjustment')}")

    if args.target in ("odds", "both"):
        url = config.ODDS_TRIFECTA_URL.format(jcd=args.jcd, date=args.date, rno=args.rno)
        print(f"\n=== odds3t ===\nURL: {url}")
        html = fetch_html(url)
        if not html:
            print("  FAILED: no HTML returned")
        else:
            (out_dir / f"odds3t_{args.date}_{args.jcd:02d}_{args.rno:02d}.html").write_text(
                html, encoding="utf-8"
            )
            print(f"  HTML size: {len(html)} chars")
            odds = parse_trifecta_odds(html)
            print(f"  parsed combinations: {len(odds)} (expected: 120)")
            # 最初の10件と最後の10件を表示
            items = list(odds.items())
            for c, o in items[:5]:
                print(f"    {c}: {o}")
            if len(items) > 10:
                print("    ...")
                for c, o in items[-5:]:
                    print(f"    {c}: {o}")


if __name__ == "__main__":
    main()
