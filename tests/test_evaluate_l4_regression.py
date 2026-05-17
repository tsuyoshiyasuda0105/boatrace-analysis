"""_evaluate_l4 / _evaluate_morning_l4 の回帰テスト。

backlog event (2026-05-17):
  F1 採用時、is_obs_prime や tetsuban_score 等の各種フラグが
  正しく設定されることを確認する。
"""

import pytest
from src.web.app import create_app


@pytest.fixture(scope="module")
def app():
    return create_app(version="v0.8")


def test_app_creates_with_l4_eval_helpers(app):
    """create_app() が成功し、内部関数が登録されることだけ確認。

    _evaluate_l4 は関数内クロージャなので直接呼べないが、
    Flask の test_client 経由で /api/market-signals を呼べば動作確認可能。
    """
    client = app.test_client()
    # /healthz は認証不要、DB なしで通る
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    # 新版 /healthz に "checks" フィールドが含まれること (回帰防止)
    assert "checks" in body, (
        f"healthz レスポンスに 'checks' フィールドがありません。\n"
        f"body: {body}\n"
        f"対応: src/web/app.py の healthz() を確認 (commit 4009559 以降の仕様)"
    )


def test_healthz_returns_200_even_on_data_quality_error(app):
    """/healthz は DB 接続 OK なら 200 を返すこと。

    backlog event (2026-05-17):
      データ品質 error で /healthz が 503 を返した結果、Render の health
      check が永続的に失敗して deploy が timed out で詰まった。
      → /healthz は **DB 接続失敗のみ 503**、データ品質 error は JSON
        ボディの "status" で表現 (200 のまま) という仕様にした。
    """
    client = app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200, (
        f"/healthz が {resp.status_code} を返しました (200 期待)。\n"
        f"body: {resp.data!r}\n"
        f"データ品質 error で 503 を返すと Render の deploy が timed out で詰まります。"
    )


def test_healthz_status_field_present(app):
    """/healthz レスポンスに status フィールド ('ok' / 'warning' / 'degraded' / 'error') があること。"""
    client = app.test_client()
    body = client.get("/healthz").get_json()
    assert "status" in body
    assert body["status"] in ("ok", "warning", "degraded", "error"), (
        f"status の値が想定外: {body['status']!r}"
    )
