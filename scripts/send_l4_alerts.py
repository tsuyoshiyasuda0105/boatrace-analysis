"""
L4 アラートメール送信スクリプト

実行タイミング:
  - 推奨: 5 分毎 (Windows Task Scheduler)
  - レース締切前に T-5min オッズが更新されたタイミング

処理フロー:
  1. 当日の全レースについて L4 マーク判定 (app.py の _detect_market_inefficiency と同等)
  2. 購読者ごとに 該当する alert_types でフィルタ
  3. min_recovery_rate 以上を抽出
  4. 既に送信済 (alert_sent) を除外
  5. ユーザー単位でまとめてメール送信
  6. 送信履歴を記録

使い方:
    python scripts/send_l4_alerts.py
    python scripts/send_l4_alerts.py --date 2026-05-13
    python scripts/send_l4_alerts.py --dry-run  # 送信せずプレビュー
"""
import argparse
import logging
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.db.connection import connect as db_connect
from src.notifications.subscribers import (
    list_active_subscribers,
    mark_sent,
    already_sent,
)
from src.notifications.mailer import send_l4_alert

# L4 マーク定義 (app.py と同期)
L4_RULES = {
    "L4_SG": {"grade": 1, "class": 1, "recovery": 258.2, "label": "L4 SG×A1",
              "bet": "3連単 1-2-3"},
    "L4_G1": {"grade": 2, "class": 1, "recovery": 242.8, "label": "L4 G1×A1",
              "bet": "3連単 1-2-3"},
    "L4_G2": {"grade": 3, "class": 1, "recovery": 242.7, "label": "L4 G2×A1",
              "bet": "3連単 1-2-3"},
    "L4_G3": {"grade": 4, "class": 1, "recovery": 149.2, "label": "L4 G3×A1",
              "bet": "3連単 1-2-3"},
    "L4_general": {"grade": 5, "class": 1, "recovery": 147.7,
                   "label": "L4 一般戦×A1", "bet": "3連単 1-2-3"},
    "L4_default": {"grade": None, "class": 1, "recovery": 160.8,
                   "label": "L4 A1", "bet": "3連単 1-2-3"},
}

EXCLUDE_VENUES = {2, 7, 10, 21, 4, 8, 19, 24}


def detect_l4_alerts(target_date: str) -> list[dict]:
    """指定日の L4 該当レースを抽出"""
    with db_connect() as conn:
        # 3連単本命払戻が 500-1000 帯 + 1号艇 + B除外 + クラス情報
        cur = conn.execute("""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   r.race_grade_number,
                   s.name AS stadium_name,
                   e.class_number,
                   pp.payout AS fav_payout
            FROM races r
            JOIN stadiums s ON r.stadium_number = s.stadium_number
            LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN (
              SELECT race_id, MIN(payout) AS payout FROM race_payouts
              WHERE bet_type = 'trifecta' GROUP BY race_id
            ) pp ON r.race_id = pp.race_id
            WHERE r.race_date = ?
        """, (target_date,))
        rows = cur.fetchall()

    alerts = []
    for (rid, stadium, rno, closed_at, grade, sname, cls, mp) in rows:
        if not mp or stadium in EXCLUDE_VENUES:
            continue
        if cls != 1:  # A1 のみ (L4 の基本条件)
            continue
        if not (500 <= mp < 1000):
            continue
        # グレード別ルール選択
        rule = None
        alert_type = None
        if grade == 1:
            rule, alert_type = L4_RULES["L4_SG"], "L4_SG"
        elif grade == 2:
            rule, alert_type = L4_RULES["L4_G1"], "L4_G1"
        elif grade == 3:
            rule, alert_type = L4_RULES["L4_G2"], "L4_G2"
        elif grade == 4:
            rule, alert_type = L4_RULES["L4_G3"], "L4_G3"
        elif grade == 5:
            rule, alert_type = L4_RULES["L4_general"], "L4_general"
        else:
            rule, alert_type = L4_RULES["L4_default"], "L4_default"

        alerts.append({
            "race_id": rid,
            "stadium_number": stadium,
            "stadium_name": sname,
            "race_number": rno,
            "race_closed_at": closed_at,
            "alert_type": alert_type,
            "label": rule["label"],
            "recovery": rule["recovery"],
            "bet": rule["bet"],
        })
    return alerts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,
                        help="対象日 (YYYY-MM-DD)、省略時は今日")
    parser.add_argument("--dry-run", action="store_true",
                        help="送信せずプレビュー表示")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    target_date = args.date or date.today().isoformat()
    print(f"[{target_date}] L4 アラート判定中...")
    alerts = detect_l4_alerts(target_date)
    print(f"  L4 該当レース: {len(alerts)} 件")
    if not alerts:
        print("  通知対象なし")
        return

    subscribers = list_active_subscribers()
    print(f"  購読者: {len(subscribers)} 名")
    if not subscribers:
        print("  購読者なし、メール送信スキップ")
        return

    # 購読者ごとに該当アラートを抽出
    sent_count = 0
    skipped_count = 0
    for sub in subscribers:
        user_alerts = []
        for a in alerts:
            # ユーザーの alert_types に含まれるか
            if a["alert_type"] not in sub["alert_types"]:
                continue
            # 最小回収率 以上か
            if a["recovery"] < sub["min_recovery_rate"]:
                continue
            # 既送信か
            if already_sent(sub["email_hash"], a["race_id"], a["alert_type"]):
                skipped_count += 1
                continue
            user_alerts.append(a)

        if not user_alerts:
            continue

        if args.dry_run:
            print(f"  [DRY-RUN] {sub['email'][:5]}*** → {len(user_alerts)} レース")
            for ua in user_alerts:
                print(f"      {ua['stadium_name']} {ua['race_number']}R "
                      f"{ua['label']} ({ua['recovery']}%)")
        else:
            ok = send_l4_alert(sub["email"], user_alerts, sub["unsubscribe_token"])
            if ok:
                for ua in user_alerts:
                    mark_sent(sub["email_hash"], ua["race_id"], ua["alert_type"])
                sent_count += 1
            else:
                print(f"  ⚠ 送信失敗: {sub['email'][:5]}***")

    print()
    if args.dry_run:
        print(f"[DRY-RUN 完了] (実送信なし、重複スキップ {skipped_count})")
    else:
        print(f"[送信完了] {sent_count} 名に送信、{skipped_count} 件は既送信スキップ")


if __name__ == "__main__":
    main()
