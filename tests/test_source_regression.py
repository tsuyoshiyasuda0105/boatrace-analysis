"""ソースコード レベルの回帰テスト (静的検査)。

backlog event (2026-05-17) で発生した個別バグについて、ソースコード内に
**修正パッチが残っていること**を確認する。リファクタ等で修正が誤って
削除されると CI で気付ける。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel_path: str) -> str:
    return (ROOT / rel_path).read_text(encoding="utf-8")


# ===== バグ 1: Layer3 補完 SQL が F1 (grade=5) を除外していた =====


def test_layer3_scope_covers_all_l4_candidates():
    """src/collectors/result_scraper.py の Layer 3 SQL が L4 採用 + 観察候補
    全て (= A1 + B除外 + prob_first 0.65-0.85) を対象とすること。

    変遷:
      v1 (旧): SG/G1/G2/G3 のみ (race_grade_number IN 1,2,3,4)
        → 一般戦 F1 採用候補が漏れる
      v2: F1 を追加 (race_grade_number = 5 AND n1≥7 AND b2≥40)
        → 一般戦 観察候補 (gen_tri / prime / r12) が漏れる
      v3 (現状): grade 制限撤廃、A1 + B除外 + prob 0.65-0.85 で全候補カバー
        → 採用 (SG/G1/G2/G3/F1) + 観察 (gen_tri / prime / r12) 全て速報スクレイプ
    """
    src = _read("src/collectors/result_scraper.py")
    # 必須フィルタ: A1 (class_number=1), B除外, prob_first 範囲
    assert "class_number = 1" in src, (
        "Layer3 SQL の A1 フィルタ (class_number=1) が見つかりません"
    )
    assert "stadium_number NOT IN" in src, (
        "Layer3 SQL の B除外フィルタ (stadium_number NOT IN ...) が見つかりません"
    )
    assert "prob_first BETWEEN" in src, (
        "Layer3 SQL の prob_first 範囲フィルタ (BETWEEN 0.65 AND 0.85) が見つかりません"
    )


# ===== バグ 2: 日別詳細で F1 の単勝/2連単/3連単が反映されない =====


def _extract_l4_daily_stats_f1_block(src: str) -> str:
    """src/web/app.py の `_l4_daily_stats` 内の F1 集計ブロックを抽出。

    `_l4_daily_stats` 関数を find して、その内部の `n1 >= 7.0 and b2 >= 40.0`
    判定 (= F1 加算条件) を探す。バッジ判定 (_evaluate_l4) ではなく
    集計 (aggregation) 側の F1 ブロックを正確に取るため。
    """
    fn_idx = src.find("def _l4_daily_stats(")
    assert fn_idx >= 0, "_l4_daily_stats 関数が見つかりません"
    # 関数本体の終わりは次の `    def ` か `    @app.route(` 等
    end_idx = src.find("\n    @app.route", fn_idx)
    fn_body = src[fn_idx: end_idx if end_idx > 0 else fn_idx + 20000]
    # F1 集計判定 (`n1 >= 7.0 and b2 >= 40.0`) を find
    f1_idx = fn_body.find("n1 >= 7.0 and b2 >= 40.0")
    assert f1_idx >= 0, (
        "_l4_daily_stats 内に `n1 >= 7.0 and b2 >= 40.0` (F1 集計判定) が見つかりません"
    )
    return fn_body[f1_idx: f1_idx + 3000]


def test_l4_daily_stats_f1_adds_to_win_exa_tri():
    """src/web/app.py の F1 ブロックで win_bets / exa_bets / tri_bets
    すべてに加算していること (日別詳細の単勝/2連単/3連単に反映)。

    バグ: 一時期 「F1 は 3連単 1-2-3 のみ」として tri_bets だけ加算していた
    時期があり、単勝/2連単のヒットが日別詳細に反映されなかった。
    修正: F1 ブロックでも 3 点全て加算。
    """
    src = _read("src/web/app.py")
    block = _extract_l4_daily_stats_f1_block(src)
    assert 'd["win_bets"] += 1' in block, (
        "_l4_daily_stats の F1 ブロックで win_bets が加算されていません。\n"
        "→ src/web/app.py の `if n1 >= 7.0 and b2 >= 40.0:` 以降の集計を確認。"
    )
    assert 'd["exa_bets"] += 1' in block, (
        "_l4_daily_stats の F1 ブロックで exa_bets (2連単) が加算されていません。"
    )
    assert 'd["tri_bets"] += 1' in block, (
        "_l4_daily_stats の F1 ブロックで tri_bets (3連単) が加算されていません。"
    )


# ===== バグ 3: F1 採用時に n_l4 にも加算されること =====


def test_l4_daily_stats_f1_increments_n_l4():
    """F1 採用時に n_l4 (L4該当数) にも +1 すること。

    バグ: F1 が「一般戦観察」(gen_f1_tri_*) としてのみ集計され、
    日別詳細の「L4該当」列が 0 のままで、F1 該当レースが表示されない問題。
    """
    src = _read("src/web/app.py")
    block = _extract_l4_daily_stats_f1_block(src)
    assert 'd["n_l4"] += 1' in block, (
        "_l4_daily_stats の F1 ブロックで n_l4 が加算されていません。\n"
        "→ 日別詳細から F1 該当レースが消えます。"
    )


def test_l4_races_for_date_includes_general_f1():
    """日別詳細一覧でも一般戦 F1 採用レースを表示すること。

    バグ: _l4_daily_stats は一般戦 F1 を n_l4 / win / exa / tri に加算する一方、
    _l4_races_for_date は grade == 5 を無条件除外していたため、
    日別ROIは 8件3的中でも詳細一覧は 7件2的中になる不整合が起きた。
    """
    src = _read("src/web/app.py")
    idx = src.find("def _l4_races_for_date(")
    assert idx >= 0, "_l4_races_for_date 関数が見つかりません"
    block = src[idx: idx + 9000]
    assert "e2.national_top_2_percent AS boat2_top2" in block
    assert "is_general_f1 = (grade == 5" in block
    assert "n1_for_f1 >= 7.0" in block
    assert "b2_for_f1 >= 40.0" in block
    assert "if grade == 5 and not is_general_f1:" in block
    assert 'rank = "L4 G++"' in block


def test_l4_races_for_date_uses_any_snapshot_l4_window_like_daily_stats():
    """日別詳細も、日別集計と同じT-X ORロジックでL4帯を判定すること。

    バグ: _l4_daily_stats は any_in_l4 で「どれかのsnapshotが5-10倍」を
    採用する一方、_l4_races_for_date は MIN(odds) だけを見ていた。
    そのため T-5でL4帯、T-1で5倍未満に人気化したレースが詳細から漏れ、
    日別9件/詳細8件の不整合が起きた。
    """
    src = _read("src/web/app.py")
    idx = src.find("def _l4_races_for_date(")
    assert idx >= 0, "_l4_races_for_date 関数が見つかりません"
    block = src[idx: idx + 9500]
    assert "oo.any_in_l4 AS any_in_l4" in block
    assert "oo.l4_odds AS l4_odds" in block
    assert "MAX(CASE WHEN odds >= 5 AND odds < 10 THEN 1 ELSE 0 END) AS any_in_l4" in block
    assert "snapshot_label='T-5min' AND odds >= 5 AND odds < 10" in block
    assert "if any_in_l4 is not None and any_in_l4 == 1:" in block
    assert "l4_odds if l4_odds is not None else fav_odds" in block


def test_morning_watch_covers_near_l4_before_exhibition():
    """展示前に本採用へ少し届かないSG/G1/G2/G3を監視候補として出すこと。

    バグ: 2026-06-09 宮島7R は G1/A1/男性/雨なしで直前に L4 帯へ入ったが、
    朝時点の prob_first=0.6366 が本採用下限 0.65 未満だったため一覧から漏れた。
    運用上は見逃し防止のため、0.60-0.65 は朝監視として表示する。
    """
    src = _read("src/web/app.py")
    idx = src.find("def _evaluate_morning_l4(")
    assert idx >= 0, "_evaluate_morning_l4 関数が見つかりません"
    block = src[idx: idx + 7000]
    assert "0.60 <= prob_first < 0.65" in block
    assert "grade in (1, 2, 3, 4)" in block
    assert '"level": "morning_watch_G1"' in block
    assert '"label": "🌅👀朝監視 G1"' in block
    assert '"is_morning_watch": True' in block
    assert '"is_reference": True' in block


def test_morning_watch_st_covers_fast_st_floaters_without_t120_odds():
    """T-120オッズを見ず、平均STの良い直前浮上候補を朝から監視すること。"""
    src = _read("src/web/app.py")
    idx = src.find("def _evaluate_morning_l4(")
    assert idx >= 0, "_evaluate_morning_l4 関数が見つかりません"
    block = src[idx: idx + 8500]
    assert "0.58 <= prob_first < 0.60" in block
    assert "grade in (1, 2, 3)" in block
    assert "avg_st_for_watch <= 0.15" in block
    assert '"level": "morning_watch_st_G1"' in block
    assert '"label": "🌅⚡朝監視ST G1"' in block
    assert '"is_morning_watch_st": True' in block
    assert "T-120 オッズは見ず" in block


def test_l4_promotion_tag_shows_when_morning_watch_st_becomes_l4():
    """朝監視STがT-5でL4本採用になった場合、小タグを出すこと。"""
    src = _read("src/web/app.py")
    assert '"promoted_from_morning_watch_st"' in src
    assert 'l4["promotion_label"] = "朝監視ST→L4"' in src

    index = _read("src/web/templates/index.html")
    assert "sig.l4.promoted_from_morning_watch_st" in index
    assert "promotion-badge promotion-watch-st" in index
    assert "朝監視ST→L4" in index

    css = _read("src/web/static/style.css")
    assert ".promotion-badge.promotion-watch-st" in css


def test_morning_watch_badge_is_prominent_in_today_picks():
    """朝監視バッジを圏外グレー扱いにせず、専用の強調表示にすること。"""
    index = _read("src/web/templates/index.html")
    assert "isMorningWatch" in index
    assert "startsWith('l4-morning_watch_')" in index
    assert "rowClass += ' is-watch'" in index
    assert "excShort = '朝監視'" in index

    css = _read("src/web/static/style.css")
    assert ".l4-badge.l4-morning_watch_G1" in css
    assert ".l4-badge.l4-morning_watch_st_G1" in css
    assert ".todays-picks-table tbody tr.is-watch" in css
    assert "@keyframes morning-watch-pulse" in css


def test_portfolio_strong_badge_is_prominent_in_today_picks():
    """10年検証ポートフォリオを強監視バッジとして目立たせること。"""
    src = _read("src/web/app.py")
    assert "def _evaluate_l4_portfolio_strong(" in src
    assert '"is_portfolio_strong": True' in src
    assert '"portfolio_recovery": 312.5' in src
    assert '"portfolio_n": 96' in src
    assert "tag_a_venues = {1, 5, 6, 9, 11, 12, 13, 16, 17, 18, 23}" in src
    assert "tag_b_venues = {5, 12, 13}" in src
    assert "tag_b_months = {2, 5, 6, 11, 12}" in src
    assert "cls == 1 and highgrade_or_f1" not in src

    index = _read("src/web/templates/index.html")
    assert "portfolio-strong-badge" in index
    assert "sig.l4.is_portfolio_strong" in index
    assert "rowClass += ' is-portfolio-strong'" in index
    assert "強監視" in index

    css = _read("src/web/static/style.css")
    assert ".portfolio-strong-badge" in css
    assert ".todays-picks-table tbody tr.is-portfolio-strong" in css
    assert "@keyframes portfolio-strong-pulse" in css


def test_today_high_roi_hides_female_mixed_and_general_references():
    """高ROI一覧では女性混合と一般参考を表示しないこと。"""
    index = _read("src/web/templates/index.html")
    assert "if (isFemaleExclusion || isGeneralReference) return;" in index
    assert "l4.classList.contains('l4-morning_general')" in index
    assert "l4.classList.contains('l4-morning_default')" in index
    assert "L4参考|一般" in index
    assert "女性混合と一般参考は高ROI一覧から非表示" in index


def test_monthly_roi_uses_two_year_window_with_quality_labels():
    """月別ROIは固定開始日ではなく2年前まで表示し、実運用/参考検証を区別すること。"""
    src = _read("src/web/app.py")
    idx = src.find("def member_strategy_monthly():")
    assert idx >= 0, "member_strategy_monthly 関数が見つかりません"
    block = src[idx: idx + 3500]
    assert 'monthly_from = "2025-07-01"' not in block
    assert "date(today.year - 2, today.month, 1).isoformat()" in block
    assert "STRICT_ODDS_DAILY_START" in block
    assert '"quality_label"] = "実運用"' in block
    assert '"quality_label"] = "参考検証"' in block
    assert '"quality_label"] = "混在"' in block
    assert "monthly_from=monthly_from" in block

    tpl = _read("src/web/templates/member_monthly.html")
    assert "{{ monthly_from }} 〜 {{ monthly_to }}" in tpl
    assert "monthly-quality-badge" in tpl
    assert "mid_132_tier_a_tri_roi" in tpl
    assert "key:'tiera'" in tpl
    assert "Tier A" in tpl
    assert "参考検証" in tpl
    assert "実運用" in tpl


# ===== バグ 4: /healthz が 503 を返すと Render deploy が timed out =====


def test_healthz_does_not_return_503_on_data_quality():
    """/healthz が data_quality_error で 503 を返さないこと。

    バグ: 以前 /healthz は data quality error >=1 で 503 を返していた。
    Render の health check が永続的に失敗し、deploy が timed out で詰まった。
    修正: DB 接続失敗のみ 503、データ品質は 200 で JSON ボディの status のみ。
    """
    src = _read("src/web/app.py")
    # healthz 関数本体を抽出
    idx = src.find("def healthz():")
    assert idx >= 0, "healthz() 関数が見つかりません"
    # 次の関数までを取得
    block = src[idx: idx + 3000]
    # data quality に基づく 503 設定が無いこと
    # "if n_err > 0:" の直後数行で http_status = 503 になっていないか
    if "n_err > 0" in block:
        # n_err 判定ブロックを抽出
        nerr_idx = block.find("n_err > 0")
        nerr_block = block[nerr_idx: nerr_idx + 500]
        assert "http_status = 503" not in nerr_block, (
            "/healthz が data_quality_errors > 0 で 503 を返す実装になっています。\n"
            "→ Render の deploy health check が失敗して deploy が timed out で詰まります。\n"
            "→ DB 接続失敗時のみ 503、データ品質は JSON ボディの status で表現してください。"
        )


# ===== バグ 5: 翌朝バッチが前夜 Layer 1 投入値を NULL で上書き =====


def test_upsert_programs_uses_coalesce():
    """src/collectors/openapi.py の upsert_programs が COALESCE upsert で
    既存値を NULL 上書きから守ること (ユーザ要望 2026-05-19)。

    バグ: 旧 INSERT OR REPLACE は Open API が NULL を返すと既存列を NULL に
    する。前夜 23:30 に Layer 1 で投入した race_closed_at が翌朝 6:30 の
    Open API バッチで消える危険がある。
    修正: ON CONFLICT (race_id) DO UPDATE SET col = COALESCE(EXCLUDED.col,
    races.col) で新値が NOT NULL のときだけ採用。
    """
    src = _read("src/collectors/openapi.py")
    # races テーブルへの COALESCE
    assert "COALESCE(EXCLUDED.race_closed_at, races.race_closed_at)" in src, (
        "upsert_programs (races) が COALESCE upsert を使っていません。\n"
        "→ ON CONFLICT (race_id) DO UPDATE SET race_closed_at = "
        "COALESCE(EXCLUDED.race_closed_at, races.race_closed_at) を確認。"
    )
    # race_entries テーブルへの COALESCE
    assert "COALESCE(EXCLUDED.national_top_1_percent, race_entries.national_top_1_percent)" in src, (
        "upsert_programs (race_entries) が COALESCE upsert を使っていません。"
    )


def test_postgres_upsert_knows_l4_daily_stats_cache_pk():
    """l4_daily_stats_cache の INSERT OR REPLACE が Postgres で上書きになること。"""
    src = _read("src/db/connection.py")
    assert '"l4_daily_stats_cache": ["race_date"]' in src


def test_b_parser_extracts_closed_at():
    """src/parsers/official_b.py が「電話投票締切予定」を抽出すること。

    バグ: 旧パーサは race_closed_at = None を常に返していた。Layer 1 B file
    には HH:MM 形式で締切時刻が含まれており、前夜表示のために抽出が必要。
    """
    src = _read("src/parsers/official_b.py")
    assert "_extract_closed_at" in src, (
        "official_b.py に _extract_closed_at 関数がありません。\n"
        "→ 翌日の race_closed_at が NULL のまま画面に「あと何分」表示できません。"
    )
    assert "電話投票締切予定" in src, (
        "official_b.py に「電話投票締切予定」パターンが見つかりません。"
    )


# ===== バグ 6: subscriber alert_types が「採用戦略」を全カバーしていない =====


def test_subscriber_default_alert_types_includes_f1():
    """DEFAULT_ALERT_TYPES に F1 (L4_general_f1) が含まれること。

    バグ: 新規購読者の alert_types デフォルトに F1 が無く、F1 メールが届かない。
    """
    from src.notifications.subscribers import DEFAULT_ALERT_TYPES
    assert "L4_general_f1" in DEFAULT_ALERT_TYPES, (
        "DEFAULT_ALERT_TYPES に L4_general_f1 が含まれていません。\n"
        "→ src/notifications/subscribers.py の DEFAULT_ALERT_TYPES を確認してください。"
    )
    assert "L4_morning_general_f1" in DEFAULT_ALERT_TYPES, (
        "DEFAULT_ALERT_TYPES に L4_morning_general_f1 (朝の F1) が含まれていません。"
    )


def test_adopted_roi_cache_missing_does_not_survive_recompute():
    """採用手法ROIの日別キャッシュが候補キャッシュ欠損のまま固定されないこと。

    バグ: market_signals キャッシュ作成前に ROI 日別キャッシュが保存されると、
    `_adopted_market_signals_cache_missing=True` の古い JSON が有効扱いされ、
    さらに再計算結果も最後の cache merge で上書きされることがあった。
    その結果、候補画面では採用だったレースが ROI 画面で 0 件になる。
    """
    src = _read("src/web/app.py")
    assert 'day_d.get("_adopted_market_signals_cache_missing")' in src
    assert "recomputed_date_set = set(missing_dates)" in src
    assert "if rdate in recomputed_date_set:" in src


def test_market_signals_cache_accepts_recent_generation_during_rollout():
    """Web/Cron の更新順が前後しても候補一覧を空にしない。"""
    src = _read("src/web/app.py")
    assert "def _market_signals_compat_cache_keys" in src
    assert 'return _market_json_response(compat_payload, "compat-stale")' in src


def test_market_signals_recent_cache_miss_self_heals():
    """直近日の全キャッシュ欠損は空応答で固定せず再生成する。"""
    src = _read("src/web/app.py")
    assert 'logger.warning("market-signals cache missing; self-healing %s"' in src
    assert "force_recompute = True" in src


def test_market_signals_recent_empty_cache_self_heals():
    """直近日の空キャッシュも正常扱いせず再生成する。"""
    src = _read("src/web/app.py")
    assert "def _is_empty_market_signals_payload" in src
    assert "and _is_empty_market_signals_payload(cached_payload)" in src
    assert "and _is_empty_market_signals_payload(stale_payload)" in src
    assert "and _is_empty_market_signals_payload(compat_payload)" in src


def test_recent_missing_market_signal_cache_keeps_raw_adopted_roi_counts():
    src = _read("src/web/app.py")
    overlay_start = src.index("def _overlay_market_signal_cache_daily")
    overlay_end = src.index("for row in cur:", overlay_start)
    overlay = src[overlay_start:overlay_end]
    assert 'day_d["_adopted_market_signals_cache_missing"] = True' in overlay
    assert 'day_d["_adopted_from_raw_fallback"] = True' in overlay
    missing_branch = overlay.split("if (", 1)[1].split("continue", 1)[0]
    assert "_clear_adopted_counts(day_d)" not in missing_branch


def test_market_signal_adopted_filter_accepts_matched_levels():
    src = _read("src/web/app.py")
    start = src.index("adopted_signal_levels = set(MARKET_SIGNAL_ADOPTED_LEVELS)")
    end = src.index("# The per-race accident watch blob", start)
    block = src[start:end]
    assert 'l4.get("matched_levels")' in block
    assert "for level in level_candidates" in block


def test_boat2_wall_daily_stats_opens_its_own_connection():
    src = _read("src/web/app.py")
    start = src.index("def _boat2_wall_daily_flags")
    end = src.index('logger.warning("boat2 wall adopted daily stats failed', start)
    block = src[start:end]
    assert "with db_connect() as conn_bw:" in block
    assert "boat2_wall_rows = conn_bw.execute(" in block
    assert "WITH target_racers AS" in block
    assert "history_prefix = {}" in block
    assert "SELECT AVG(rr.start_timing)" not in block


def test_roi_daily_stats_do_not_count_unsettled_market_signal_candidates():
    src = _read("src/web/app.py")
    assert "def _settled_race_ids_for_range" in src
    assert "def _dates_with_settled_results" in src
    assert "def _clear_roi_result_metrics" in src
    overlay_start = src.index("def _overlay_market_signal_cache_daily")
    overlay_end = src.index("for row in cur:", overlay_start)
    overlay = src[overlay_start:overlay_end]
    assert "settled_race_ids = _settled_race_ids_for_range(from_date, to_date)" in overlay
    assert "if race_id not in settled_race_ids:" in overlay
    assert "continue" in overlay.split("if race_id not in settled_race_ids:", 1)[1].split("pay = sum", 1)[0]


def test_roi_cache_only_clears_unsettled_day_metrics():
    src = _read("src/web/app.py")
    start = src.index("def _l4_daily_stats_cache_only")
    end = src.index("def _l4_daily_stats(", start)
    block = src[start:end]
    assert "settled_dates = _dates_with_settled_results(from_date, to_date)" in block
    assert "if rdate not in settled_dates:" in block
    assert "_clear_roi_result_metrics(day)" in block
    assert 'day["_roi_unsettled_result_guard"] = True' in block


def test_reference_market_signals_are_not_today_roi_candidates():
    src = _read("src/web/app.py")
    start = src.index("def _market_pick_rows_for_display(")
    end = src.index("@app.route", start)
    block = src[start:end]
    assert 'if l4.get("is_reference"):' in block
    assert 'if l4.get("is_reference") and level in ("morning_general", "general"):' not in block

    template = _read("src/web/templates/index.html")
    start = template.index("function renderTodaysPicks()")
    end = template.index("function renderPickRows", start)
    block = template[start:end]
    assert "if (isRef) {" in block
    assert "if (isRef && (level === 'morning_general' || level === 'general'))" not in block


def test_render_jobs_build_derived_start_stats_before_roi_signals():
    scheduler = _read("scripts/render_regular_scheduler.py")
    assert "def run_derived_start_stats(" in scheduler
    signal_start = scheduler.index("def run_signal_refresh_slot(")
    signal_end = scheduler.index("def run_roi_history_slot", signal_start)
    signal_block = scheduler[signal_start:signal_end]
    assert "ok = run_derived_start_stats(today, today)" in signal_block
    assert signal_block.index("run_derived_start_stats(today, today)") < signal_block.index(
        '"scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", today'
    )

    nightly_start = scheduler.index("def run_nightly(")
    nightly_end = scheduler.index("def main()", nightly_start)
    nightly_block = scheduler[nightly_start:nightly_end]
    assert "ok &= run_derived_start_stats(today, tomorrow)" in nightly_block
    assert nightly_block.index("run_derived_start_stats(today, tomorrow)") < nightly_block.index(
        '"scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", tomorrow'
    )

    refresh = _read("scripts/refresh_race_detail_after_exhibition.py")
    assert '"scripts/build_derived_start_stats.py", "--from", target_date, "--to", target_date' in refresh
    assert refresh.index('"scripts/build_derived_start_stats.py"') < refresh.index('"scripts/generate_start_predictions.py"')
    exhibition_signal_start = refresh.index("def refresh_market_signals_if_needed(")
    exhibition_signal_end = refresh.index("def _parse_race_close_jst", exhibition_signal_start)
    exhibition_signal_block = refresh[exhibition_signal_start:exhibition_signal_end]
    assert exhibition_signal_block.index('"scripts/build_derived_start_stats.py"') < exhibition_signal_block.index(
        '"scripts/prewarm_strategy_pages.py", "--mode", "signals", "--date", target_date'
    )
