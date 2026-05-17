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


def test_layer3_scope_includes_general_grade5_f1():
    """src/collectors/result_scraper.py の L4 候補 SQL に「F1 一般戦」が含まれること。

    バグ: 以前は `race_grade_number IN (1, 2, 3, 4)` のみで F1 (grade=5) が
    Layer3 補完対象から除外されていた。
    修正: `OR (race_grade_number = 5 AND national_top_1_percent >= 7 AND
              boat2 national_top_2_percent >= 40)` を追加。
    """
    src = _read("src/collectors/result_scraper.py")
    # SQL 内に F1 条件が含まれていること
    assert "race_grade_number = 5" in src, (
        "Layer3 補完 SQL に grade=5 (F1) が含まれていません。\n"
        "→ scrape_results_for_pending_races() の SQL を確認してください。"
    )
    assert "national_top_1_percent" in src
    assert "national_top_2_percent" in src


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


# ===== バグ 5: subscriber alert_types が「採用戦略」を全カバーしていない =====


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
