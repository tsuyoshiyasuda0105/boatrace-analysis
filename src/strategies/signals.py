"""Pure market-signal evaluators extracted from the web application."""


def _prefer_adopted_signal_over_general200(selected, adopted):
    """Keep adopted strategy labels visible when general200 also matches.

    `l4_general_200` is a useful overlay, but it is not part of the
    current adopted-strategy set shown in ROI pages. When both match the
    same race, prefer the adopted strategy for consistency between the
    ROI dashboard and the live "ROIが高いレース" list.
    """
    if not selected or not adopted:
        return selected
    selected_level = str(selected.get("level") or "")
    if selected_level not in {"l4_general_200", "morning_watch_l4_general_200"}:
        return selected

    merged = dict(adopted)
    matched_levels = []
    for level in (
        *(selected.get("matched_levels") or []),
        selected.get("level"),
        *(adopted.get("matched_levels") or []),
        adopted.get("level"),
    ):
        if level and level not in matched_levels:
            matched_levels.append(level)
    matched_labels = []
    for label in (
        *(selected.get("matched_labels") or []),
        selected.get("label"),
        *(adopted.get("matched_labels") or []),
        adopted.get("label"),
    ):
        if label and label not in matched_labels:
            matched_labels.append(label)
    matched_bets = []
    for bet in (
        *(selected.get("matched_bets") or []),
        selected.get("bet"),
        *(adopted.get("matched_bets") or []),
        adopted.get("bet"),
    ):
        if bet and bet not in matched_bets:
            matched_bets.append(bet)
    matched_recoveries = []
    for recovery in (
        *(selected.get("matched_recoveries") or []),
        selected.get("recovery"),
        *(adopted.get("matched_recoveries") or []),
        adopted.get("recovery"),
    ):
        if recovery is not None and recovery not in matched_recoveries:
            matched_recoveries.append(recovery)

    merged["matched_levels"] = matched_levels
    merged["matched_labels"] = matched_labels
    merged["matched_bets"] = matched_bets
    merged["matched_recoveries"] = matched_recoveries
    merged["is_l4_general_200"] = bool(selected.get("is_l4_general_200"))
    merged["general200_hit_rate"] = selected.get("general200_hit_rate")
    merged["general200_recovery"] = selected.get("general200_recovery")
    merged["general200_n"] = selected.get("general200_n")
    merged["general200_boat2_top2"] = selected.get("general200_boat2_top2")
    merged["general200_boat2_exhibition_time"] = selected.get("general200_boat2_exhibition_time")
    merged["general200_boat3_exhibition_time"] = selected.get("general200_boat3_exhibition_time")
    merged["general200_boat2_faster"] = selected.get("general200_boat2_faster")
    merged["general200_ex_st"] = selected.get("general200_ex_st")
    merged["general200_ex_st_good"] = selected.get("general200_ex_st_good")
    return merged


def _allow_market_signal_with_female(signal, n_female_count: int, *, ROI_STRATEGY_KEYS=()) -> bool:
    """Gate live ROI candidates before they reach the betting list."""
    if not signal:
        return False
    if int(n_female_count or 0) <= 0:
        return True
    level = str(signal.get("level") or "")
    if (
        signal.get("is_exacta_niche")
        or signal.get("is_trifecta_niche")
        or signal.get("is_win_niche")
        or level in set(ROI_STRATEGY_KEYS)
    ):
        return True
    return bool(signal.get("allow_female_market_signal"))


def _pick_best_market_signal(*signals, ACCIDENT_DENT_STRATEGIES=()):
    adopted_priority_levels = {
        "a1_ace_motor_123_corr_tri", "g23_optb_tri", "gmkf_132_tri",
        "shimonoseki_123_tri", "tsu_124_tri", "amagasaki_143_tri",
        "amagasaki_13_exa", "omura_13_exa", "ashiya_boat4_exa",
        "hamanako_14_exa", "omura_14_exa", "tokuyama_123_tri",
        "tokuyama_13_exa", "shimonoseki_132_tri", "kojima_124_tri",
        "kojima_13_exa", "marugame_123_tri", "omura_123_tri",
        "omura_132_tri", "tsu_123_tri", "suminoe_123_tri",
        "miyajima_tide_132_tri", "gamagori_tide_132_tri", "marugame_tide_123_tri",
        "fukuoka_tide_132_tri", "fukuoka_ex12_b_exa", "fukuoka_tri124_c",
        "fukuoka_123_late_foot_tri", "gamagori_123_general_practical_tri",
        "gamagori_13_exa", "tokuyama_12a_exa", "tokoname_12_late_a_exa",
        "tokoname_14_winter_exa", "tokoname_123_late_exst_tri", "toda_123_tri",
        "tsu_143_tri", "kojima_123_tri", "gamagori_123_tri", "naruto_123_tri",
        "karatsu_132_tri", "tri134_acc2_ex3_tri", "omura_132_weak2_ex3_tri",
        "wakamatsu_13_weak2_strong3_exa", "heiwajima_13_acc2_late_exa",
        "tamagawa_13_weak_sashi2_exa", "tamagawa_13_acc2n30_m3_40_exa",
        "tamagawa_123_fl3_n3_30_m2_35_tri", "hamanako_12_pts3_m23_exa",
        "kojima_12_acc3_m3_n23_exa", "edogawa_13_acc2_n23_m3_exa",
        "kiryu_13_fl2_n23_exa", "ashiya_13_pts2_m23_exa",
        "amagasaki_12_acc3_fl3_exa", "omura_13_acc2_fl2_m23_exa",
        "marugame_13_pts2_m23_exa", "tokoname_coursefit_boat2_win",
        "tokoname_coursefit_boat3_general_win", "biwako_coursefit_boat4_gap10_general_win",
        "shimonoseki_coursefit_boat2_win", "biwako_coursefit_boat4_gap5_general_win",
        "biwako_coursefit_boat4_rank1_general_win", "biwako_coursefit_boat4_gap10_all_win",
    }
    adopted_priority_levels.update(
        strategy.key for strategy in ACCIDENT_DENT_STRATEGIES
    )
    adopted_priority_levels.update({
        "morning_watch_SG", "morning_watch_G1", "morning_watch_G2",
        "morning_watch_st_SG", "morning_watch_st_G1", "morning_watch_st_G2",
        "morning_watch_g23_optb", "morning_watch_shimonoseki_123_tri",
        "morning_watch_ashiya_boat4_lift", "morning_watch_tokoname_123_late_exst_tri",
        "morning_watch_omura_123_tri", "morning_watch_tri143_a12",
        "morning_watch_gmkf_132_tri", "morning_watch_gamagori_adopted",
        "morning_watch_tri134_acc2_ex3_tri", "morning_watch_omura_132_weak2_ex3_tri",
        "morning_watch_fukuoka_tide_132_tri", "morning_watch_miyajima_tide_132_tri",
        "morning_watch_gamagori_tide_132_tri", "morning_watch_marugame_tide_123_tri",
        "morning_watch_tsu_123_tri", "morning_watch_suminoe_123_tri",
        "morning_watch_tamagawa_13_acc2n30_m3_40_exa",
        "morning_watch_tamagawa_123_fl3_n3_30_m2_35_tri",
        "morning_watch_hamanako_12_pts3_m23_exa",
        "morning_watch_kojima_12_acc3_m3_n23_exa",
        "morning_watch_edogawa_13_acc2_n23_m3_exa",
        "morning_watch_kiryu_13_fl2_n23_exa", "morning_watch_ashiya_13_pts2_m23_exa",
        "morning_watch_amagasaki_12_acc3_fl3_exa", "morning_watch_omura_13_acc2_fl2_m23_exa",
        "morning_watch_marugame_13_pts2_m23_exa",
    })

    def _is_adopted_priority_signal(sig):
        level = sig.get("level") if sig else None
        return level in adopted_priority_levels

    valid = []
    best = None
    best_recovery = float("-inf")
    for sig in signals:
        if not sig:
            continue
        valid.append(sig)
    if not valid:
        return None
    preferred = [sig for sig in valid if _is_adopted_priority_signal(sig)]
    candidate_pool = preferred or valid
    for sig in candidate_pool:
        try:
            rec = float(sig.get("recovery")) if sig.get("recovery") is not None else float("-inf")
        except (TypeError, ValueError):
            rec = float("-inf")
        if best is None or rec > best_recovery:
            best = sig
            best_recovery = rec
    if best is None:
        return None
    merged = dict(best)
    merged["matched_levels"] = [s.get("level") for s in valid if s.get("level")]
    merged["matched_labels"] = [s.get("label") for s in valid if s.get("label")]
    merged["matched_bets"] = [s.get("bet") for s in valid if s.get("bet")]
    merged["matched_recoveries"] = [s.get("recovery") for s in valid if s.get("recovery") is not None]
    return merged


def _evaluate_candidate_134_signal(
    stadium,
    grade,
    race_number,
    natl_1=None,
    age=None,
    course1=None,
    boat2_motor_top2=None,
    avg_st=None,
    avg_st_n=None,
    weather=None,
    n_female=0,
    target_date_iso=None,
):
    try:
        month = int(str(target_date_iso)[5:7]) if target_date_iso else 0
    except (TypeError, ValueError):
        month = 0
    try:
        rn = int(race_number) if race_number is not None else 0
    except (TypeError, ValueError):
        rn = 0
    try:
        n1 = float(natl_1) if natl_1 is not None else 0.0
    except (TypeError, ValueError):
        n1 = 0.0
    try:
        a1 = int(age) if age is not None else None
    except (TypeError, ValueError):
        a1 = None
    try:
        c1 = int(course1) if course1 is not None else 0
    except (TypeError, ValueError):
        c1 = 0
    try:
        m2 = float(boat2_motor_top2) if boat2_motor_top2 is not None else 0.0
    except (TypeError, ValueError):
        m2 = 0.0
    try:
        dst1 = float(avg_st) if avg_st is not None else None
    except (TypeError, ValueError):
        dst1 = None
    try:
        dstn1 = int(avg_st_n) if avg_st_n is not None else 0
    except (TypeError, ValueError):
        dstn1 = 0

    female_count = int(n_female or 0)
    cand1 = (
        female_count == 0
        and c1 in (1, 2)
        and stadium in (5, 12, 13)
        and month in (2, 5, 6, 11, 12)
        and 7.5 <= n1 < 8.5
        and a1 is not None and 40 <= a1 <= 49
    )
    cand3 = (
        female_count == 0
        and c1 in (1, 2)
        and stadium in (1, 5, 6, 9, 11, 12, 13, 16, 17, 18, 23)
        and 10 <= rn <= 12
        and 7.5 <= n1 < 8.5
        and a1 is not None and 40 <= a1 <= 49
        and m2 >= 45.0
    )
    highgrade_or_f1 = grade in (1, 2, 3, 4) or (grade == 5 and n1 >= 7.0 and m2 >= 40.0)
    cand4 = (
        female_count == 0
        and highgrade_or_f1
        and 9 <= rn <= 11
        and dst1 is not None and dst1 < 0.160
        and dstn1 >= 6
        and a1 is not None and 40 <= a1 <= 49
        and 7.5 <= n1 < 8.5
        and m2 >= 50.0
        and weather != 3
        and stadium in (1, 5, 6, 9, 11, 12, 13, 16, 17, 18, 23)
    )
    matched = []
    if cand1:
        matched.append(("cand1", "候補1", 204.0, 1189))
    if cand3:
        matched.append(("cand3", "候補3", 259.3, 150))
    if cand4:
        matched.append(("cand4", "候補4", 293.3, 9))
    if not matched:
        return None
    primary = matched[-1]
    return {
        "level": primary[0],
        "label": primary[1],
        "recovery": primary[2],
        "bet": "3連単 1-2-3",
        "n": primary[3],
        "rank": primary[0],
        "rank_label": primary[1],
        "natl_1": natl_1,
        "local_1": None,
        "is_reference": False,
        "candidate_keys": [m[0] for m in matched],
        "candidate_labels": [m[1] for m in matched],
        "tetsuban_score": 5 if primary[0] == "cand4" else (4 if primary[0] == "cand3" else 3),
        "tetsuban_label": primary[1],
    }


def _evaluate_l4_general_200(stadium, grade, cls, natl_1=None,
                             boat2_top2=None, boat2_exhibition_time=None,
                             boat3_exhibition_time=None, ex_st=None):
    """Retired L4 general-race watch; kept as a no-op compatibility hook."""
    return None


def _compute_tetsuban(base: dict, race_no: int) -> tuple[int, str]:
    """鉄板度スコア (1-6) と表示ラベルを計算 (backlog item 11)。

    条件 (各 1 点、合計 0-6):
      + 高グレード (SG/G1/G2)
      + F1 一般戦 (一般×国1%≥7×2号40)
      + race_number 11 または 12 (prime / メインレース)
      + race_number 12 (最終レース、上に+1で 12R は計 2 点)
        → 12R は実質 +2 点扱いで「鉄板側に振れる」
          ※ 11R も prime bonus が付くが 12R bonus は付かない
      + L4 1c80 (1号艇1コース 1着率 80%+)
      + L4 PRO (ベテラン × ST × 展示)
      + L4++ (国1%≥7 + 地1%≥9)
      + 1号艇国1%≥7 のみ満たす L4+ (中間)

    戻り値: (score 1-6, label) — 鉄板度マーク "★×N" + 評価名
    """
    level = base.get("level", "")
    is_high_grade = level in ("SG", "G1", "G2")
    is_f1 = bool(base.get("is_f1"))
    is_prime_r = race_no in (11, 12)
    is_final_r = race_no == 12
    is_1c80 = bool(base.get("is_1c80"))
    is_pro  = bool(base.get("is_l4_pro"))
    rank    = base.get("rank")
    is_plus_plus = rank == "plus_plus"
    is_plus      = rank == "plus"

    score = 0
    if is_high_grade:    score += 1
    if is_f1:            score += 1
    if is_prime_r:       score += 1
    if is_final_r:       score += 1   # 12R は更に +1 (prime と合わせて +2)
    if is_1c80:          score += 1
    if is_pro:           score += 1
    if is_plus_plus:     score += 1
    elif is_plus:        score += 0   # plus 単独は弱いので加点しない

    # スコアを 1-5 の星に圧縮
    stars = 1
    if score >= 5: stars = 5
    elif score >= 4: stars = 4
    elif score >= 3: stars = 3
    elif score >= 2: stars = 2
    else: stars = 1

    # ラベル (backlog item 9: ダイヤモンド廃止 → ★×N 表記に統一)
    if stars >= 5:
        lab = f"鉄板 {stars}★"
    elif stars == 4:
        lab = f"強推 {stars}★"
    elif stars == 3:
        lab = f"推奨 {stars}★"
    elif stars == 2:
        lab = f"候補 {stars}★"
    else:
        lab = f"通常 {stars}★"
    return stars, lab


def _detect_niche_signals(preds: list[dict], conditions: dict) -> list[dict]:
    """
    検証済の「ニッチ大穴シグナル」を検出する。
    Returns: 該当艇のシグナル情報リスト
    """
    signals = []
    cls_map = {1: "A1", 2: "A2", 3: "B1", 4: "B2"}

    # 艇番→予測情報のマップ
    by_boat = {p.get("boat_number"): p for p in (preds or [])}
    # 艇番→直前情報 (tilt含む) のマップ
    boats_cond = (conditions or {}).get("boats", {})

    for boat_num in [1, 2, 3, 4, 5, 6]:
        p = by_boat.get(boat_num)
        bc = boats_cond.get(boat_num) or boats_cond.get(str(boat_num)) or {}
        if not p:
            continue
        tilt = bc.get("tilt_adjustment")
        cls = p.get("class_number")
        cls_label = cls_map.get(cls, "?")

        if tilt is None:
            continue

        # 検証済シグナル: 艇5 + tilt=3.0 + A2選手 (P>0=95%)
        if boat_num == 5 and tilt == 3.0 and cls == 2:
            signals.append({
                "level": "ultra",
                "boat_number": 5,
                "tilt": tilt,
                "class_label": cls_label,
                "title": "🔥🔥🔥 ニッチ大穴チャンス",
                "desc": f"艇5 + チルト3.0 + A2選手の組合せ。Backtest ROI +118.29% (n=41, CI [-13%, +290%], P(ROI>0)=95.0%)",
                "recommend": "三連単 5-X-Y 上位10通り買い推奨",
                "warning": "n=41 のサンプル。実運用は要慎重",
            })
        # 検証済シグナル: 艇5 + tilt=3.0 + A1+A2 (P>0=66-71%)
        elif boat_num == 5 and tilt == 3.0 and cls in (1, 2):
            signals.append({
                "level": "high",
                "boat_number": 5,
                "tilt": tilt,
                "class_label": cls_label,
                "title": "🔥🔥 大まくり勝負賭け",
                "desc": f"艇5 + チルト3.0 + {cls_label}選手。Backtest ROI +22.60% (n=73, P(ROI>0)=65.5%)",
                "recommend": "三連単 5-X-Y 上位10通り買い検討",
                "warning": "サンプル小、慎重に",
            })
        # 検証済シグナル: 艇4 + tilt 0.5-1.5 (まくり狙い、最も n 大)
        elif boat_num == 4 and tilt is not None and 0.5 <= tilt <= 1.5:
            signals.append({
                "level": "mid",
                "boat_number": 4,
                "tilt": tilt,
                "class_label": cls_label,
                "title": "🔥 4号艇まくり狙い",
                "desc": f"艇4 + チルト{tilt:+.1f}。Backtest ROI -11.09% (n=2,202, P(ROI>0)=10.0%)",
                "recommend": "通常買いより 4 絡みの妙味あり",
                "warning": "+EV ではないが通常戦略より優位",
            })
        # 検証済シグナル: 艇5 tilt>=1.5 (大まくり狙い、A2絡みでない場合)
        elif boat_num == 5 and tilt is not None and tilt >= 1.5 and cls not in (1, 2):
            signals.append({
                "level": "low",
                "boat_number": 5,
                "tilt": tilt,
                "class_label": cls_label,
                "title": "⚙️ 5号艇 伸びセッティング",
                "desc": f"艇5 + チルト{tilt:+.1f} + {cls_label}選手 (上位級ではない)",
                "recommend": "効果限定的、参考情報",
                "warning": "級別が低くまくり成立率低い",
            })
        # 一般的なプラスチルト警告 (情報)
        elif tilt is not None and tilt >= 1.0 and boat_num >= 3:
            signals.append({
                "level": "info",
                "boat_number": boat_num,
                "tilt": tilt,
                "class_label": cls_label,
                "title": f"⚙️ 艇{boat_num} プラスチルト",
                "desc": f"チルト{tilt:+.1f} = 伸び/まくり狙いの可能性",
                "recommend": "参考情報",
                "warning": None,
            })

    return signals
