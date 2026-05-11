"""
チルト戦略の徹底検証

目的:
  「艇5 tilt=3.0 で P(ROI>0)=17.1%」「艇4 tilt 0.5-1.5 で P>0=10%」
  が本当に再現性のあるシグナルか、複数次元で検証する。

検証次元:
  1. 年別の安定性 (2022-2025)
  2. 会場別の効果差
  3. 三連単での効果 (単勝でなく三連単で +EV を狙う)
  4. 級別との交互作用 (A1選手のチルト+ vs B級のチルト+)
  5. レース時間帯 (節初日 vs 後半)

成功基準:
  - 複数年で同方向の改善 → 再現性あり
  - 三連単で CI上限が +0%を超える → 真の +EV 候補
  - 級別との交互作用が論理的 → 過学習でない
"""
import sqlite3
import random
import statistics
from typing import List

DB = "data/boatrace.db"
N_BOOT = 2000
random.seed(42)


def bootstrap_ci(payouts: List[float], bet: float = 100) -> dict:
    n = len(payouts)
    if n == 0:
        return {"n": 0, "roi": None, "lo": None, "hi": None, "p0": None}
    rois = []
    for _ in range(N_BOOT):
        sample = random.choices(payouts, k=n)
        rois.append(sum(sample) / n / bet - 1.0)
    rois.sort()
    return {
        "n": n,
        "roi": sum(payouts) / n / bet - 1.0,
        "lo": rois[int(N_BOOT * 0.025)],
        "hi": rois[int(N_BOOT * 0.975)],
        "p0": sum(1 for r in rois if r > 0) / N_BOOT,
    }


def fetch_win_payouts(conn, boat: int, where: str) -> List[float]:
    sql = f"""
        SELECT CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = {boat}
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = {boat}
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = {boat}
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win'
                                  AND pp.combination='{boat}'
        WHERE {where}
    """
    return [float(row[0]) for row in conn.execute(sql).fetchall()]


def fetch_trifecta_payouts(conn, fav_boat: int, where: str, combos: List[str]) -> List[float]:
    """指定艇が勝った時の三連単払戻を取得 (1着指定)。
    combos: 買う三連単パターン (例 ['4-1-X', '4-2-X'])
    実装: 1着が fav_boat の三連単のみ集計 → 配当を払戻として返す
    買い目別の n_bets で割って ROI 計算
    """
    payouts = []
    # 1着=fav_boat となるレースを抽出し、各レースの三連単結果を取る
    sql = f"""
        SELECT r.race_id,
               pp_tri.combination as winning_combo, pp_tri.payout
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = {fav_boat}
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = {fav_boat}
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = {fav_boat}
        LEFT JOIN race_payouts pp_tri ON pp_tri.race_id = r.race_id AND pp_tri.bet_type='trifecta'
        WHERE {where}
    """
    for race_id, win_combo, payout in conn.execute(sql).fetchall():
        # 1レースで複数の三連単買い目を購入する想定: N_BETS = len(combos)
        # 各買い目はそれぞれ 100円。的中時に payout を 1回 GET。
        # 簡略化: 1着が fav_boat なら、1着-X-Y で X,Y 不問の三連単買い → 当たり率
        # ここでは「1着=fav_boat 縛り」で X-Y を考えず、的中ケースの三連単配当を払戻として返す
        # ROI 計算は: total_payout / (n_races * 100 * n_bets) - 1
        if win_combo and win_combo.startswith(f"{fav_boat}-"):
            payouts.append(float(payout))
        else:
            payouts.append(0.0)
    return payouts


def show(label: str, r: dict):
    if r["n"] == 0:
        print(f"  {label:<60} [no data]")
        return
    flag = ""
    if r["hi"] > 0:
        flag = " *** CI+"
    elif r["p0"] > 0.05:
        flag = " * P>5%"
    print(f"  {label:<60} n={r['n']:>6,}  ROI={r['roi']:>+8.2%}  CI=[{r['lo']:>+7.2%}, {r['hi']:>+7.2%}]  P>0={r['p0']:>5.1%}{flag}")


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # 検証1: 年別の安定性 (艇4 tilt 0.5-1.5, 艇5 tilt=3.0)
    # =========================================================
    print("=" * 110)
    print("[Test 1] 年別安定性検証 (単勝)")
    print("=" * 110)

    for boat, tilt_where, label_base in [
        (4, "p.tilt_adjustment >= 0.5 AND p.tilt_adjustment <= 1.5", "艇4 tilt 0.5-1.5"),
        (5, "p.tilt_adjustment = 3.0", "艇5 tilt=3.0"),
        (5, "p.tilt_adjustment >= 1.5", "艇5 tilt >= 1.5"),
    ]:
        print(f"\n--- {label_base} ---")
        for year in [2022, 2023, 2024, 2025]:
            where = f"{tilt_where} AND r.race_date >= '{year}-01-01' AND r.race_date < '{year+1}-01-01'"
            payouts = fetch_win_payouts(conn, boat, where)
            r = bootstrap_ci(payouts)
            show(f"  {year}年", r)

    # =========================================================
    # 検証2: 会場別効果差
    # =========================================================
    print()
    print("=" * 110)
    print("[Test 2] 会場別効果 (艇5 tilt=3.0, 主要会場)")
    print("=" * 110)
    cur = conn.execute(
        """
        SELECT r.stadium_number, s.name, COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout, 0) ELSE 0 END) as avg_p
        FROM races r
        JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = 5
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 5
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 5
        JOIN stadiums s ON r.stadium_number = s.stadium_number
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='5'
        WHERE p.tilt_adjustment = 3.0
        GROUP BY r.stadium_number
        HAVING n >= 5
        ORDER BY (avg_p - 100) DESC
        """
    )
    print(f"{'場':<10} {'n':>4} {'avg_pay':>10} {'ROI':>10}")
    for sid, name, n, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {name:<8} {n:>4} {ap or 0:>10.1f} {roi:>+10.2%}")

    # =========================================================
    # 検証3: 三連単での効果
    # =========================================================
    print()
    print("=" * 110)
    print("[Test 3] 三連単 (1着=該当艇) ROI 推定")
    print("(注: 1着X-Y-Zの全120組合せ均等買い前提でなく、的中時の払戻分布を見る)")
    print("=" * 110)

    for boat, tilt_where, label_base in [
        (4, "p.tilt_adjustment >= 0.5 AND p.tilt_adjustment <= 1.5", "艇4 tilt 0.5-1.5"),
        (5, "p.tilt_adjustment = 3.0", "艇5 tilt=3.0"),
        (5, "p.tilt_adjustment >= 1.5", "艇5 tilt >= 1.5"),
        (6, "p.tilt_adjustment >= 1.5", "艇6 tilt >= 1.5"),
    ]:
        # 1着=該当艇のレースで実際の三連単配当を取得
        cur = conn.execute(f"""
            SELECT pp_tri.payout
            FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = {boat}
            JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = {boat}
            JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = {boat}
            LEFT JOIN race_payouts pp_tri ON pp_tri.race_id = r.race_id AND pp_tri.bet_type='trifecta'
            WHERE {tilt_where} AND res.finishing_position = 1
        """)
        payouts_when_won = [float(row[0]) for row in cur.fetchall() if row[0]]
        if not payouts_when_won:
            print(f"  {label_base:<25} (no winning races)")
            continue
        # 該当条件の全レース数 (= 1着取れたか取れなかったか)
        cur2 = conn.execute(f"""
            SELECT COUNT(*) FROM races r
            JOIN race_entries e ON r.race_id = e.race_id AND e.boat_number = {boat}
            JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = {boat}
            WHERE {tilt_where}
        """)
        n_total = cur2.fetchone()[0]
        n_wins = len(payouts_when_won)
        win_rate = n_wins / n_total
        avg_payout = sum(payouts_when_won) / n_wins
        median_payout = statistics.median(payouts_when_won)
        max_payout = max(payouts_when_won)
        # 全120通り買った場合の ROI: avg_winning_payout * win_rate / (120 * 100) - 1
        # 1着=該当艇の三連単20通り (X-Y は 5*4 = 20) 買った場合: ... / (20 * 100) - 1
        roi_20bets = avg_payout * win_rate / (20 * 100) - 1
        roi_10bets_focused = avg_payout * win_rate / (10 * 100) - 1
        print(f"  {label_base:<25} n_total={n_total:>5}, 1着取得={n_wins:>4}({win_rate:>5.1%})  "
              f"avg配当={avg_payout:>7,.0f}円  median={median_payout:>7,.0f}円  最大={max_payout:>7,.0f}円")
        print(f"    {' ':<23} 1着-X-Y 20通り買い: ROI={roi_20bets:>+8.2%}, "
              f"絞り込み 10通り買い: ROI={roi_10bets_focused:>+8.2%}")

    # =========================================================
    # 検証4: 級別との交互作用 (A1選手のチルト戦略 vs B級)
    # =========================================================
    print()
    print("=" * 110)
    print("[Test 4] 級別との交互作用 (艇5 tilt=3.0)")
    print("=" * 110)
    class_names = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}
    for cls_num, cls_label in class_names.items():
        where = f"p.tilt_adjustment = 3.0 AND e.class_number = {cls_num}"
        payouts = fetch_win_payouts(conn, 5, where)
        r = bootstrap_ci(payouts)
        show(f"艇5 tilt=3.0 + 級別{cls_label}", r)

    # =========================================================
    # 検証5: 「同レース内に複数のプラスチルト艇」の場合
    # =========================================================
    print()
    print("=" * 110)
    print("[Test 5] 同レース内のプラスチルト艇数別、レース全体の三連単高配当率")
    print("=" * 110)
    cur = conn.execute(
        """
        WITH positive_tilt AS (
            SELECT p.race_id, COUNT(*) as n_positive_tilt
            FROM race_previews p
            WHERE p.tilt_adjustment >= 0.5
            GROUP BY p.race_id
        )
        SELECT
            CASE
                WHEN pt.n_positive_tilt IS NULL THEN '0艇'
                WHEN pt.n_positive_tilt = 1 THEN '1艇'
                WHEN pt.n_positive_tilt = 2 THEN '2艇'
                WHEN pt.n_positive_tilt >= 3 THEN '3艇以上'
            END as tier,
            COUNT(*) as n_races,
            AVG(pp.payout) as avg_payout,
            SUM(CASE WHEN pp.payout >= 10000 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as p_10k_plus,
            SUM(CASE WHEN pp.payout >= 50000 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as p_50k_plus
        FROM races r
        LEFT JOIN positive_tilt pt ON r.race_id = pt.race_id
        JOIN race_payouts pp ON r.race_id = pp.race_id AND pp.bet_type = 'trifecta'
        GROUP BY tier
        ORDER BY MIN(COALESCE(pt.n_positive_tilt, 0))
        """
    )
    print(f"{'プラスチルト艇数':<14} {'n_races':>10} {'avg三連単':>12} {'P(>10k)':>10} {'P(>50k)':>10}")
    print("-" * 70)
    for tier, n, ap, p10, p50 in cur.fetchall():
        print(f"  {tier:<12} {n:>10,} {ap or 0:>12,.0f} {p10:>10.3f} {p50:>10.3f}")

    conn.close()


if __name__ == "__main__":
    main()
