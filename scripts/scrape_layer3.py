"""
Layer 3 スクレイピング統合エントリポイント (boatrace.jp 公式サイト)

使い方:
    python scripts/scrape_layer3.py                              # 今日 / parts+odds
    python scripts/scrape_layer3.py --date 2026-05-08
    python scripts/scrape_layer3.py --backfill 30 --targets parts
    python scripts/scrape_layer3.py --targets parts,odds --force --verbose

注意:
  - REQUEST_INTERVAL_SECONDS=2.0 を厳守。並列実行禁止
  - 1日 ~150レース × 2秒/req × 2ターゲット = 約10分/日
  - 365日バックフィルは数十時間かかるので夜間運用前提
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors import beforeinfo as beforeinfo_collector
from src.collectors import odds as odds_collector
from src.collectors import original_exhibition as original_exhibition_collector


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str, help="YYYY-MM-DD 形式 (省略時は今日)")
    p.add_argument("--backfill", type=int, default=0,
                   help="指定日数分過去にさかのぼって取得")
    p.add_argument("--targets", type=str, default="parts,odds,original",
                   help="parts,odds,original をカンマ区切りで指定")
    p.add_argument("--force", action="store_true",
                   help="既取得分も再取得")
    p.add_argument("--no-save-html", action="store_true",
                   help="生 HTML を保存しない")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def main():
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    targets = {t.strip() for t in args.targets.split(",") if t.strip()}
    invalid = targets - {"parts", "odds", "original"}
    if invalid:
        print(f"[ERROR] unknown targets: {invalid}", file=sys.stderr)
        sys.exit(2)

    target_date = date.fromisoformat(args.date) if args.date else date.today()
    if args.backfill > 0:
        dates = [target_date - timedelta(days=i) for i in range(args.backfill + 1)]
    else:
        dates = [target_date]

    save_html = not args.no_save_html

    for d in dates:
        if "parts" in targets:
            s = beforeinfo_collector.collect_for_date(
                d, force=args.force, save_html=save_html
            )
            print(
                f"{s['date']} parts: targeted={s['races_targeted']} "
                f"fetched={s['races_fetched']} parts={s['parts_inserted']} "
                f"previews_supp={s['previews_supplemented']}"
            )
        if "odds" in targets:
            s = odds_collector.collect_for_date(
                d, force=args.force, save_html=save_html
            )
            print(
                f"{s['date']} odds : targeted={s['races_targeted']} "
                f"fetched={s['races_fetched']} odds={s['odds_inserted']}"
            )
        if "original" in targets:
            s = original_exhibition_collector.collect_for_date(
                d, force=args.force, save_html=save_html
            )
            print(
                f"{s['date']} original: targeted={s['races_targeted']} "
                f"fetched={s['pages_fetched']} found={s['races_found']} "
                f"rows={s['rows_inserted']}"
            )


if __name__ == "__main__":
    main()
