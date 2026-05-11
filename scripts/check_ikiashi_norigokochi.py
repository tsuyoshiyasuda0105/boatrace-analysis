"""
「行き足」「乗り心地」の代理指標検証

代理指標の定義:
  行き足 (ターン後加速):
    - 展示タイム (周回タイム): exhibition_time
    - 展示ST: start_timing_exhibition
    - 「ST良い + 展示タイム良い」= 行き足が良い艇
    - 「ST良い + 展示タイム悪い」= 出足だけ、行き足悪い
    - 「ST悪い + 展示タイム良い」= 起き足遅いがターン後仕上がってる

  乗り心地 (操縦性):
    - tilt_adjustment: チルト角度の調整
    - 標準チルト (-0.5) との乖離 = ハンドリング調整中
    - weight_adjustment: 体重調整 (斤量) も補正の証拠
"""
import sqlite3

DB = "data/boatrace.db"


def main():
    conn = sqlite3.connect(DB)

    # =========================================================
    # 検証1: 「行き足」= 展示タイム × 展示ST の組合せ
    # =========================================================
    print("=" * 80)
    print("[Test 1] Iki-ashi proxy: 展示タイム順位 x 展示ST順位 で 1号艇 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH ex_ranks AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as ex_rk,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.start_timing_exhibition ASC) as st_rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL AND p.start_timing_exhibition IS NOT NULL
        )
        SELECT
            CASE
                WHEN er.ex_rk <= 2 AND er.st_rk <= 2 THEN 'A: 両方上位 (好行き足)'
                WHEN er.ex_rk <= 2 AND er.st_rk >= 4 THEN 'B: 展示OK・ST悪 (起き足遅)'
                WHEN er.ex_rk >= 4 AND er.st_rk <= 2 THEN 'C: 展示悪・ST良 (出足のみ)'
                WHEN er.ex_rk >= 4 AND er.st_rk >= 4 THEN 'D: 両方下位 (不調)'
                ELSE 'E: 中間'
            END as ikiashi_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM ex_ranks er
        JOIN race_results res ON er.race_id = res.race_id AND er.boat_number = res.boat_number
        LEFT JOIN race_payouts pp ON pp.race_id = er.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE er.boat_number = 1
        GROUP BY ikiashi_tier
        ORDER BY ikiashi_tier
        """
    )
    print(f"{'tier':<32} {'n':>10} {'1go_winR':>10} {'ROI':>10}")
    print("-" * 70)
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {tier:<30} {n:>10,} {wr:>10.3f} {roi:>+10.2%}")

    # =========================================================
    # 検証2: 同上を 全艇 (1-6) でやって 1着率分布
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 2] 全艇: 「行き足上位」(展示&ST両方上位) -> 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH ex_ranks AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as ex_rk,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.start_timing_exhibition ASC) as st_rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL AND p.start_timing_exhibition IS NOT NULL
        )
        SELECT er.boat_number as bn,
               CASE WHEN er.ex_rk <= 2 AND er.st_rk <= 2 THEN '行き足上位' ELSE 'その他' END as flag,
               COUNT(*) as n,
               AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
               AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM ex_ranks er
        JOIN race_results res ON er.race_id = res.race_id AND er.boat_number = res.boat_number
        LEFT JOIN race_payouts pp ON pp.race_id = er.race_id
                                  AND pp.bet_type='win'
                                  AND pp.combination = CAST(er.boat_number AS TEXT)
        GROUP BY bn, flag
        HAVING n >= 100
        ORDER BY bn, flag
        """
    )
    print(f"{'boat':<6} {'flag':<14} {'n':>10} {'win_rate':>10} {'ROI':>10}")
    print("-" * 60)
    for bn, flag, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        marker = " *" if flag == '行き足上位' and wr > 0.5 else ""
        print(f"  {bn:<4} {flag:<14} {n:>10,} {wr:>10.3f} {roi:>+10.2%}{marker}")

    # =========================================================
    # 検証3: 「乗り心地」= tilt 調整値
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 3] Norigokochi proxy: tilt 調整値別 1号艇 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.tilt_adjustment IS NULL THEN 'NULL'
                WHEN p.tilt_adjustment < -1.0 THEN 'A: <-1.0 (down深め)'
                WHEN p.tilt_adjustment < -0.5 THEN 'B: -1.0〜-0.5'
                WHEN p.tilt_adjustment < 0.0  THEN 'C: -0.5〜0.0 (標準域)'
                WHEN p.tilt_adjustment = 0.0  THEN 'D: 0.0 (フラット)'
                WHEN p.tilt_adjustment <= 0.5 THEN 'E: 0.0〜0.5 (やや上向)'
                ELSE 'F: >0.5 (up大きく)'
            END as tilt_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM races r
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = r.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE p.tilt_adjustment IS NOT NULL
        GROUP BY tilt_tier
        ORDER BY tilt_tier
        """
    )
    print(f"{'tilt tier':<28} {'n':>10} {'1go_winR':>10} {'ROI':>10}")
    print("-" * 65)
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {tier:<26} {n:>10,} {wr:>10.3f} {roi:>+10.2%}")

    # =========================================================
    # 検証4: 「乗り心地」weight_adjustment (体重調整=斤量)
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 4] weight_adjustment (斤量) 別 1号艇 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        SELECT
            CASE
                WHEN p.weight_adjustment IS NULL THEN 'NULL'
                WHEN p.weight_adjustment = 0     THEN 'A: 0 (無調整)'
                WHEN p.weight_adjustment <= 1.0  THEN 'B: 0〜1kg'
                WHEN p.weight_adjustment <= 2.0  THEN 'C: 1〜2kg'
                WHEN p.weight_adjustment <= 3.0  THEN 'D: 2〜3kg'
                ELSE 'E: 3kg以上'
            END as w_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr
        FROM races r
        JOIN race_previews p ON r.race_id = p.race_id AND p.boat_number = 1
        JOIN race_results res ON r.race_id = res.race_id AND res.boat_number = 1
        WHERE p.weight_adjustment IS NOT NULL
        GROUP BY w_tier
        ORDER BY w_tier
        """
    )
    print(f"{'weight_adj':<22} {'n':>10} {'1go_winR':>10}")
    print("-" * 50)
    for tier, n, wr in cur.fetchall():
        print(f"  {tier:<20} {n:>10,} {wr:>10.3f}")

    # =========================================================
    # 検証5: 行き足 ＋ 乗り心地 の合成
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 5] 行き足良し ＋ tilt 標準域 の合成 (1号艇)")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH ex_ranks AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as ex_rk,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.start_timing_exhibition ASC) as st_rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL AND p.start_timing_exhibition IS NOT NULL
        )
        SELECT
            CASE WHEN er.ex_rk <= 2 AND er.st_rk <= 2 THEN '行き足良' ELSE '行き足普通' END as ikiashi,
            CASE WHEN p1.tilt_adjustment BETWEEN -0.5 AND 0.0 THEN 'tilt標準'
                 WHEN p1.tilt_adjustment IS NULL THEN 'tilt不明'
                 ELSE 'tilt調整中' END as tilt_state,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as wr,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM ex_ranks er
        JOIN race_previews p1 ON er.race_id = p1.race_id AND p1.boat_number = er.boat_number
        JOIN race_results res ON er.race_id = res.race_id AND er.boat_number = res.boat_number
        LEFT JOIN race_payouts pp ON pp.race_id = er.race_id AND pp.bet_type='win' AND pp.combination='1'
        WHERE er.boat_number = 1
        GROUP BY ikiashi, tilt_state
        HAVING n >= 100
        ORDER BY ikiashi, tilt_state
        """
    )
    print(f"{'行き足':<12} {'乗り心地':<14} {'n':>10} {'1go_winR':>10} {'ROI':>10}")
    print("-" * 65)
    for ika, tlt, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {ika:<10} {tlt:<14} {n:>10,} {wr:>10.3f} {roi:>+10.2%}")

    # =========================================================
    # 検証6: 穴目への影響 (非1号艇の行き足良し)
    # =========================================================
    print()
    print("=" * 80)
    print("[Test 6] 2-6号艇に「行き足良」がいる時の 1号艇 1着率")
    print("=" * 80)
    cur = conn.execute(
        """
        WITH ex_ranks AS (
            SELECT r.race_id, p.boat_number,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.exhibition_time ASC) as ex_rk,
                   RANK() OVER (PARTITION BY r.race_id ORDER BY p.start_timing_exhibition ASC) as st_rk
            FROM races r
            JOIN race_previews p ON r.race_id = p.race_id
            WHERE p.exhibition_time IS NOT NULL AND p.start_timing_exhibition IS NOT NULL
        ),
        threat AS (
            SELECT race_id, MAX(CASE WHEN ex_rk <= 2 AND st_rk <= 2 AND boat_number >= 2 THEN boat_number ELSE 0 END) as best_outer
            FROM ex_ranks GROUP BY race_id
        )
        SELECT
            CASE
                WHEN t.best_outer = 0 THEN '外艇に行き足良なし'
                WHEN t.best_outer = 2 THEN '2号艇 行き足良'
                WHEN t.best_outer = 3 THEN '3号艇 行き足良'
                WHEN t.best_outer >= 4 THEN '4-6号艇 行き足良 (要注意)'
            END as threat_tier,
            COUNT(*) as n,
            AVG(CASE WHEN res.finishing_position=1 THEN 1.0 ELSE 0.0 END) as p1_winR,
            AVG(CASE WHEN res.finishing_position=1 THEN COALESCE(pp.payout,0) ELSE 0 END) as ap
        FROM threat t
        JOIN race_results res ON t.race_id = res.race_id AND res.boat_number = 1
        LEFT JOIN race_payouts pp ON pp.race_id = t.race_id AND pp.bet_type='win' AND pp.combination='1'
        GROUP BY threat_tier
        ORDER BY MIN(t.best_outer)
        """
    )
    print(f"{'外艇脅威':<28} {'n':>10} {'1go_winR':>10} {'1go_ROI':>10}")
    print("-" * 65)
    for tier, n, wr, ap in cur.fetchall():
        roi = (ap or 0)/100 - 1
        print(f"  {tier:<26} {n:>10,} {wr:>10.3f} {roi:>+10.2%}")

    conn.close()


if __name__ == "__main__":
    main()
