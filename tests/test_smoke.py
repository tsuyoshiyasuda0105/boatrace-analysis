"""
Smoke tests: 各モジュールが import 可能か確認する最小テスト。

各 import を独立した assert にして、CI 失敗時に **どのモジュールが原因か**
が pytest 出力ですぐ分かるようにする。
"""
import importlib


def _try_import(mod: str):
    try:
        importlib.import_module(mod)
    except Exception as e:
        raise AssertionError(f"failed to import {mod}: {type(e).__name__}: {e}") from e


def test_import_config():
    _try_import("config")


def test_import_db_connection():
    _try_import("src.db.connection")


def test_import_features_builder():
    _try_import("src.features.builder")


def test_import_models_train():
    _try_import("src.models.train")


def test_import_models_calibration():
    _try_import("src.models.calibration")


def test_import_models_cascade():
    _try_import("src.models.cascade")


def test_import_models_cascade_per_winner():
    _try_import("src.models.cascade_per_winner")


def test_import_evaluation_bootstrap_ci():
    _try_import("src.evaluation.bootstrap_ci")


def test_import_evaluation_evaluate_with_payouts():
    _try_import("src.evaluation.evaluate_with_payouts")


def test_import_web_predictor():
    _try_import("src.web.predictor")


def test_import_web_auth():
    _try_import("src.web.auth")


def test_import_web_app():
    _try_import("src.web.app")


def test_create_app():
    """Flask アプリが起動できることだけ確認 (モデル無しでも create_app は通る)"""
    from src.web.app import create_app
    app = create_app(version="v0.8")
    assert app is not None
    # /healthz は常に動く (DBアクセスなし)
    client = app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
