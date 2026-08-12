from src.deploy_info import deploy_revision
from src.web.app import create_app


def test_deploy_revision_is_short_and_non_secret(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b184ececa18681349acf889ba7703b7338f32680")
    assert deploy_revision() == "b184ececa186"


def test_healthz_exposes_deploy_revision(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "1234567890abcdef1234567890abcdef12345678")
    app = create_app(version="v0.8")
    body = app.test_client().get("/healthz").get_json()
    assert body["revision"] == "1234567890ab"
