"""
Smoke tests: 重要モジュールが import 可能か確認するだけの最小テスト。

CI で `pytest tests/` を回した時に、構文エラーや明らかな依存欠落を検出する。
"""
import importlib


def test_config_imports():
    importlib.import_module("config")


def test_features_builder_imports():
    importlib.import_module("src.features.builder")


def test_models_train_imports():
    importlib.import_module("src.models.train")


def test_evaluation_modules_import():
    for mod in [
        "src.evaluation.evaluate_with_payouts",
        "src.evaluation.bootstrap_ci",
        "src.evaluation.test_set_eval",
        "src.evaluation.true_value_bet",
        "src.evaluation.niche_scanner",
        "src.evaluation.market_calibration",
    ]:
        importlib.import_module(mod)


def test_web_app_imports():
    importlib.import_module("src.web.app")


def test_create_app():
    """Flask アプリが起動できることだけ確認 (モデル無しでも create_app は通る)"""
    from src.web.app import create_app
    app = create_app(version="v0.8")
    assert app is not None
    # /healthz は常に動く
    client = app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
