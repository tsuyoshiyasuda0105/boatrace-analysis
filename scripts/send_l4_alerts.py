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

# Windows cp932 でも絵文字を出力できるよう stdout を UTF-8 化 (Python 3.7+)
# これを忘れると UnicodeEncodeError でメール送信処理が止まる致命的バグになる
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass  # Python 3.6 以下や TextIOWrapper でないストリームは無視

from src.db.connection import connect as db_connect
from src.notifications.subscribers import (
    list_active_subscribers,
    mark_sent,
    already_sent,
)
from src.notifications.mailer import send_l4_alert
# 単一情報源 (DRY): app.py と共通の L4 定義を使う
from src.evaluation.l4_strategy import (
    EXCLUDE_VENUES,
    lookup_rule,
    l4_rank,
    is_l4_payout_range,
    is_b_excluded,
)

# グレード番号 → alert_type のマッピング (購読者の alert_types フィルタ用)
GRADE_TO_ALERT_TYPE = {
    1: "L4_SG",
    2: "L4_G1",
    3: "L4_G2",
    4: "L4_G3",
    5: "L4_general",
}


def detect_l4_alerts(target_date: str) -> list[dict]:
    """指定日の L4 該当レースを抽出

    優先順位:
      1. T-5min オッズ (締切前 5 分のスナップショット) ← メール通知用
      2. T-15min オッズ (締切前 15 分)
      3. final 確定払戻 (レース後)

    締切前のメール送信を成立させるため、odds_trifecta テーブルの
    pre-race スナップショットを優先的に使う。
    最低オッズ × 100 = 三連単本命払戻と等価。

    除外条件 (Web UI / 朝メール / ROI集計と整合):
      - B 除外会場 (戸田・蒲郡・三国・芦屋・常滑・下関・平和島・大村)
      - cls != 1 (A1 以外)
      - L4 帯外 (500-1000 円)
      - final (事後判定)
      - ☔ 雨 (weather_number=3): backtest で ROI ~100% break-even
      - ♀ レース内女性あり (案A): 男性のみ 180.8% / 女性混入 134-158%
    """
    with db_connect() as conn:
        # 共通 SELECT 部分
        base_query = """
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   r.race_grade_number,
                   s.name AS stadium_name,
                   e.class_number,
                   pp.payout AS fav_payout,
                   pp.src AS payout_src
            FROM races r
            JOIN stadiums s ON r.stadium_number = s.stadium_number
            LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            JOIN (
              -- 1. T-5min を最優先 (締切前メール送信用)
              SELECT race_id, MIN(odds)*100 AS payout, 'T-5min' AS src,
                     1 AS priority
              FROM odds_trifecta
              WHERE snapshot_label = 'T-5min'
              GROUP BY race_id
              UNION ALL
              -- 2. T-15min (T-5min がまだ無いレースに使う)
              SELECT race_id, MIN(odds)*100 AS payout, 'T-15min' AS src,
                     2 AS priority
              FROM odds_trifecta
              WHERE snapshot_label = 'T-15min'
              GROUP BY race_id
              UNION ALL
              -- 3. 確定払戻 (レース後)
              SELECT race_id, MIN(payout) AS payout, 'final' AS src,
                     3 AS priority
              FROM race_payouts
              WHERE bet_type = 'trifecta'
              GROUP BY race_id
            ) all_payouts ON r.race_id = all_payouts.race_id
            JOIN (
              -- 各レースで最も優先度の高い (T-5min > T-15min > final) を選ぶ
              SELECT race_id, MIN(priority) AS min_priority FROM (
                SELECT race_id, 1 AS priority FROM odds_trifecta
                  WHERE snapshot_label = 'T-5min'
                UNION ALL
                SELECT race_id, 2 AS priority FROM odds_trifecta
                  WHERE snapshot_label = 'T-15min'
                UNION ALL
                SELECT race_id, 3 AS priority FROM race_payouts
                  WHERE bet_type = 'trifecta'
              ) GROUP BY race_id
            ) best ON r.race_id = best.race_id
                  AND all_payouts.race_id = best.race_id
            JOIN (
              -- 上記と整合させるため payout 行も priority 一致
              SELECT race_id, payout, src,
                CASE src WHEN 'T-5min' THEN 1 WHEN 'T-15min' THEN 2 ELSE 3 END AS priority
              FROM (
                SELECT race_id, MIN(odds)*100 AS payout, 'T-5min' AS src
                  FROM odds_trifecta WHERE snapshot_label = 'T-5min' GROUP BY race_id
                UNION ALL
                SELECT race_id, MIN(odds)*100 AS payout, 'T-15min' AS src
                  FROM odds_trifecta WHERE snapshot_label = 'T-15min' GROUP BY race_id
                UNION ALL
                SELECT race_id, MIN(payout) AS payout, 'final' AS src
                  FROM race_payouts WHERE bet_type = 'trifecta' GROUP BY race_id
              )
            ) pp ON pp.race_id = r.race_id AND pp.priority = best.min_priority
            WHERE r.race_date = ?
            GROUP BY r.race_id
        """
        # シンプル版: T-5min > T-15min > final で COALESCE
        # + 1号艇選手の国1%/局1% も取得 (L4+/L4++ ランク判定用)
        # + 2号艇 国2連率 (一般戦 F1 判定用)
        # + race_previews.weather_number (☔ 雨除外フィルタ用、2026-05-21 追加)
        cur = conn.execute("""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   r.race_grade_number,
                   s.name AS stadium_name,
                   e.class_number,
                   e.national_top_1_percent, e.local_top_1_percent,
                   e.racer_name,
                   COALESCE(t5.payout, t15.payout, final.payout) AS fav_payout,
                   CASE
                     WHEN t5.payout IS NOT NULL THEN 'T-5min'
                     WHEN t15.payout IS NOT NULL THEN 'T-15min'
                     ELSE 'final'
                   END AS payout_src,
                   e2.national_top_2_percent AS boat2_top2,
                   pv.weather_number AS weather,
                   COALESCE(fem.n_female, 0) AS n_female
            FROM races r
            JOIN stadiums s ON r.stadium_number = s.stadium_number
            LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            LEFT JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
            LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
            LEFT JOIN (
              SELECT ef.race_id, COUNT(*) AS n_female
                FROM race_entries ef
                JOIN racers rc ON rc.racer_number = ef.racer_number
               WHERE rc.gender = 2
               GROUP BY ef.race_id
            ) fem ON fem.race_id = r.race_id
            LEFT JOIN (
              SELECT race_id, MIN(odds)*100 AS payout FROM odds_trifecta
              WHERE snapshot_label='T-5min' GROUP BY race_id
            ) t5 ON r.race_id = t5.race_id
            LEFT JOIN (
              SELECT race_id, MIN(odds)*100 AS payout FROM odds_trifecta
              WHERE snapshot_label='T-15min' GROUP BY race_id
            ) t15 ON r.race_id = t15.race_id
            LEFT JOIN (
              SELECT race_id, MIN(payout) AS payout FROM race_payouts
              WHERE bet_type='trifecta' GROUP BY race_id
            ) final ON r.race_id = final.race_id
            WHERE r.race_date = ?
              AND COALESCE(t5.payout, t15.payout, final.payout) IS NOT NULL
        """, (target_date,))
        rows = cur.fetchall()

    alerts = []
    for row in rows:
        (rid, stadium, rno, closed_at, grade, sname, cls,
         natl_1, local_1, racer_name, mp, payout_src, boat2_top2, weather,
         n_female) = row
        if not mp or is_b_excluded(stadium):
            continue
        if cls != 1:  # A1 のみ (L4 の基本条件)
            continue
        if not is_l4_payout_range(mp):
            continue
        # ☔ 雨レース除外 (weather_number=3): backtest で ROI ~100% (break-even)
        # のためメール対象外。Web UI / 朝メール / ROI集計と整合。
        if weather == 3:
            continue
        # ♀ 案A 女性除外: レース内に女性 1 名でもいると ROI 低下のため対象外。
        if n_female and n_female > 0:
            continue
        # final (確定後) のレースは「事後判定」なので通知しない
        if payout_src == "final":
            continue

        # 一般戦は F1 条件 (国1%≥7 + 2号 top_2≥40) を満たす場合のみ通す
        if grade == 5:
            try:
                n1_check = float(natl_1) if natl_1 is not None else 0.0
                b2_check = float(boat2_top2) if boat2_top2 is not None else 0.0
            except (TypeError, ValueError):
                n1_check = b2_check = 0.0
            if not (n1_check >= 7.0 and b2_check >= 40.0):
                continue
            # F1 該当: 専用 alert_type、推奨 ROI 204%
            try:
                n1 = float(natl_1) if natl_1 is not None else 0.0
                l1 = float(local_1) if local_1 is not None else 0.0
            except (TypeError, ValueError):
                n1 = l1 = 0.0
            alerts.append({
                "race_id": rid,
                "stadium_number": stadium,
                "stadium_name": sname,
                "race_number": rno,
                "race_closed_at": closed_at,
                "alert_type": "L4_general_f1",
                "label": "🌟L4 G++ (一般×国1%≥7×2号40)",
                "recovery": 204.0,
                "bet": "3連単 1-2-3",
                "payout_src": payout_src,
                "fav_payout": int(mp),
                "mode": "confirmed",
                "rank": "f1", "rank_label": "L4 G++ F1",
                "rank_emoji": "🌟",
                "natl_1": n1, "local_1": l1,
                "racer_name": racer_name or "",
            })
            continue

        # 単一情報源からルール取得 (SG/G1/G2/G3 用)
        rule = lookup_rule(grade, cls)
        if rule is None:
            continue
        alert_type = GRADE_TO_ALERT_TYPE.get(grade, "L4_default")

        # サブランク判定 (l4_strategy.py の関数を使用)
        rank_code, rank_label, rank_emoji, rec_override = l4_rank(natl_1, local_1)

        # ランク上位の場合は recovery を上書き
        effective_recovery = rec_override if rec_override is not None else rule["recovery"]
        label_with_rank = f"{rank_emoji}{rule['label']}"
        if rank_code != "base":
            label_with_rank += f" ({rank_label})"

        # 後段で使う変数を保持
        try:
            n1 = float(natl_1) if natl_1 is not None else 0.0
            l1 = float(local_1) if local_1 is not None else 0.0
        except (TypeError, ValueError):
            n1 = l1 = 0.0

        alerts.append({
            "race_id": rid,
            "stadium_number": stadium,
            "stadium_name": sname,
            "race_number": rno,
            "race_closed_at": closed_at,
            "alert_type": alert_type,
            "label": label_with_rank,
            "recovery": effective_recovery,
            "bet": rule["bet"],
            "payout_src": payout_src,
            "fav_payout": int(mp),
            "mode": "confirmed",
            # ▼ ランク情報 (メール本文に表示)
            "rank": rank_code,
            "rank_label": rank_label,
            "rank_emoji": rank_emoji,
            "natl_1": n1,
            "local_1": l1,
            "racer_name": racer_name or "",
        })
    return alerts


def detect_morning_l4_candidates(target_date: str) -> list[dict]:
    """朝判定モード: predictions テーブルの 1号艇 prob_first を使って
    オッズ確定前に L4 候補レースを抽出する。

    抽出条件:
      - 1号艇 A1 ∧ prob_first ∈ [0.65, 0.85)  ← 本命 500-1000円帯候補
      - B 除外会場でない
      - 雨レース (weather_number=3) を除外
      - ♀ レース内女性あり (案A) を除外
      - A2 派生 (cls=2) は対象外
      - 一般戦 (grade=5) は F1 条件
          「1号艇 国1%≥7 ∧ 2号艇 国2連率≥40」
        を満たすもののみ通す (OOS Tier 1 検証 ROI 204%)。
        非該当の 一般戦 はメール対象外。

    alert_type は "L4_morning_*" で確定版と区別。一般戦 F1 は
    "L4_morning_general_f1" の専用タイプ。
    """
    with db_connect() as conn:
        cur = conn.execute("""
            SELECT r.race_id, r.stadium_number, r.race_number, r.race_closed_at,
                   r.race_grade_number,
                   s.name AS stadium_name,
                   e.class_number,
                   e.national_top_1_percent, e.local_top_1_percent,
                   e.racer_name,
                   p.prob_first,
                   pv.weather_number,
                   e2.national_top_2_percent,
                   COALESCE(fem.n_female, 0) AS n_female
            FROM races r
            JOIN stadiums s ON r.stadium_number = s.stadium_number
            LEFT JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 1
            LEFT JOIN race_entries e2 ON r.race_id = e2.race_id AND e2.boat_number = 2
            JOIN predictions p ON r.race_id = p.race_id AND p.boat_number = 1
            LEFT JOIN race_previews pv ON pv.race_id = r.race_id AND pv.boat_number = 1
            LEFT JOIN (
              SELECT ef.race_id, COUNT(*) AS n_female
                FROM race_entries ef
                JOIN racers rc ON rc.racer_number = ef.racer_number
               WHERE rc.gender = 2
               GROUP BY ef.race_id
            ) fem ON fem.race_id = r.race_id
            WHERE r.race_date = ?
              AND p.prob_first IS NOT NULL
        """, (target_date,))
        rows = cur.fetchall()

    alerts = []
    for row in rows:
        (rid, stadium, rno, closed_at, grade, sname, cls,
         natl_1, local_1, racer_name, prob_first, weather, boat2_top2,
         n_female) = row
        if is_b_excluded(stadium):
            continue
        if prob_first is None:
            continue

        # A1 のみ、0.65-0.85 帯
        if not (cls == 1 and 0.65 <= prob_first < 0.85):
            continue

        # 雨レース (weather_number=3) はメール対象外
        if weather == 3:
            continue

        # ♀ 案A 女性除外: レース内に女性 1 名でもいると ROI 低下のため対象外。
        if n_female and n_female > 0:
            continue

        # 一般戦は F1 条件を満たす場合のみ通す
        if grade == 5:
            try:
                n1 = float(natl_1) if natl_1 is not None else 0.0
                b2 = float(boat2_top2) if boat2_top2 is not None else 0.0
            except (TypeError, ValueError):
                n1 = b2 = 0.0
            if not (n1 >= 7.0 and b2 >= 40.0):
                continue
            # F1 該当: 専用ラベル
            effective_recovery = 204.0
            morning_label = "🌅🌟朝L4 G++ 候補 (一般×国1%≥7×2号40)"
            morning_alert_type = "L4_morning_general_f1"
            try:
                n1_f = float(natl_1) if natl_1 is not None else 0.0
                l1_f = float(local_1) if local_1 is not None else 0.0
            except (TypeError, ValueError):
                n1_f = l1_f = 0.0
            alerts.append({
                "race_id": rid, "stadium_number": stadium,
                "stadium_name": sname, "race_number": rno,
                "race_closed_at": closed_at,
                "alert_type": morning_alert_type,
                "label": morning_label,
                "recovery": effective_recovery,
                "bet": "3連単 1-2-3 (確定後)",
                "payout_src": "morning_predict",
                "fav_payout": None,
                "prob_first": prob_first,
                "mode": "morning",
                "rank": "f1", "rank_label": "L4 G++ F1",
                "rank_emoji": "🌟",
                "natl_1": n1_f, "local_1": l1_f,
                "racer_name": racer_name or "",
            })
            continue

        # ルール取得 (SG/G1/G2/G3 用、既存ロジック)
        rule = lookup_rule(grade, cls)
        if rule is None:
            continue

        # ランク判定 (A1 のみサブランク適用)
        rank_code, rank_label, rank_emoji, rec_override = l4_rank(natl_1, local_1)

        effective_recovery = rec_override if rec_override is not None else rule["recovery"]

        # 朝モード用ラベル (🌅 を先頭に)
        morning_label = f"🌅{rank_emoji}朝{rule['label']}"
        if rank_code != "base":
            morning_label += f" ({rank_label})"
        morning_label += f" 候補"

        # alert_type を確定版と区別
        confirmed_at = GRADE_TO_ALERT_TYPE.get(grade, "L4_default")
        morning_alert_type = f"L4_morning_{confirmed_at.replace('L4_', '')}"

        try:
            n1 = float(natl_1) if natl_1 is not None else 0.0
            l1 = float(local_1) if local_1 is not None else 0.0
        except (TypeError, ValueError):
            n1 = l1 = 0.0

        alerts.append({
            "race_id": rid,
            "stadium_number": stadium,
            "stadium_name": sname,
            "race_number": rno,
            "race_closed_at": closed_at,
            "alert_type": morning_alert_type,
            "label": morning_label,
            "recovery": effective_recovery,
            "bet": rule["bet"] + " (確定後)",
            "payout_src": "morning_predict",
            "fav_payout": None,
            "prob_first": prob_first,
            "mode": "morning",
            "rank": rank_code,
            "rank_label": rank_label,
            "rank_emoji": rank_emoji,
            "natl_1": n1,
            "local_1": l1,
            "racer_name": racer_name or "",
        })
    return alerts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None,
                        help="対象日 (YYYY-MM-DD)、省略時は今日")
    parser.add_argument("--mode", choices=["confirmed", "morning", "both"],
                        default="confirmed",
                        help="confirmed=確定オッズベース(従来) / morning=朝予測候補 / both=両方")
    parser.add_argument("--dry-run", action="store_true",
                        help="送信せずプレビュー表示")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    target_date = args.date or date.today().isoformat()
    print(f"[{target_date}] L4 アラート判定中 (mode={args.mode})...")

    # モードに応じて検出ソースを選択
    alerts: list[dict] = []
    if args.mode in ("confirmed", "both"):
        confirmed = detect_l4_alerts(target_date)
        print(f"  確定 L4 (T-5/T-15): {len(confirmed)} 件")
        if confirmed:
            n_pp = sum(1 for a in confirmed if a.get("rank") == "plus_plus")
            n_p  = sum(1 for a in confirmed if a.get("rank") == "plus")
            n_b  = len(confirmed) - n_pp - n_p
            print(f"    内訳: 🥇L4++ {n_pp} / 🥈L4+ {n_p} / ⭐L4 {n_b}")
        alerts.extend(confirmed)

    if args.mode in ("morning", "both"):
        morning = detect_morning_l4_candidates(target_date)
        print(f"  朝 L4 候補 (予測): {len(morning)} 件")
        if morning:
            n_pp = sum(1 for a in morning if a.get("rank") == "plus_plus")
            n_p  = sum(1 for a in morning if a.get("rank") == "plus")
            n_a2 = sum(1 for a in morning if a.get("rank") == "a2")
            n_b  = len(morning) - n_pp - n_p - n_a2
            print(f"    内訳: 🥇L4++ {n_pp} / 🥈L4+ {n_p} / ⭐L4 {n_b} / 📈A2派生 {n_a2}")
        alerts.extend(morning)

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
