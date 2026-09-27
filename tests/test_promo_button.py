"""期間限定特典: 管理画面でリンクを設定 → 登録者のメニューにボタン → /promo で Drive へ。"""
import re
import sqlite3
from datetime import date

import pytest

from src.web import app as web_app
from src.web import promo_bp

DRIVE = "https://drive.google.com/file/d/abc123/view?usp=sharing"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "settings.db"
    monkeypatch.setattr(promo_bp, "_connect", lambda: sqlite3.connect(path))
    promo_bp.clear_cache()
    yield path
    promo_bp.clear_cache()


def _client(role=None):
    app = web_app.create_app()
    app.config.update(TESTING=True, SECRET_KEY="test")
    client = app.test_client()
    if role:
        with client.session_transaction() as s:
            s["is_member"] = True
            s["role"] = role
            s["auth_provider"] = "local"
            s["email"] = "admin@example.com"
            s["csrf_token"] = "tok"
    return client


def _save(db, **values):
    with sqlite3.connect(db) as conn:
        promo_bp.save_settings(conn, values, "test")
    promo_bp.clear_cache()


# ---- 入力の確認 ----
@pytest.mark.parametrize("url", [
    "http://drive.google.com/file/d/x/view",            # https でない
    "https://evil.example.com/drive.google.com",         # 別サイト
    "https://drive.google.com.evil.example/file",        # 似せたドメイン
    "javascript:alert(1)",
])
def test_rejects_non_drive_links(url):
    values, error = promo_bp.validate(url, "", "")
    assert error and not values


def test_accepts_drive_and_docs_links_and_defaults_label():
    for url in (DRIVE, "https://docs.google.com/document/d/abc/edit"):
        values, error = promo_bp.validate(f"  {url} ", "", "2026-10-31")
        assert error is None
        assert values == {"promo_url": url, "promo_label": "期間限定特典", "promo_until": "2026-10-31"}


def test_rejects_long_label_and_bad_date():
    assert promo_bp.validate(DRIVE, "あ" * 21, "")[1]
    assert promo_bp.validate(DRIVE, "", "10/31")[1]


def test_empty_url_turns_promo_off():
    values, error = promo_bp.validate("", "", "")
    assert error is None and values["promo_url"] == ""


# ---- 表示するかどうか ----
def test_expired_or_empty_promo_is_hidden(db):
    assert promo_bp.current_promo() is None
    _save(db, promo_url=DRIVE, promo_label="特典", promo_until="2026-10-31")
    assert promo_bp.current_promo(date(2026, 10, 31))["url"] == DRIVE   # 期限の日までは出す
    assert promo_bp.current_promo(date(2026, 11, 1)) is None


def test_db_failure_hides_button_without_breaking(monkeypatch):
    promo_bp.clear_cache()

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(promo_bp, "_connect", boom)
    assert promo_bp.current_promo() is None
    promo_bp.clear_cache()


# ---- 画面 ----
def test_free_member_sees_button_guest_does_not(db):
    _save(db, promo_url=DRIVE, promo_label="300本の検証リスト", promo_until="")
    html = _client("free_member").get("/guide").get_data(as_text=True)
    assert "nav-btn-promo" in html and "300本の検証リスト" in html
    assert re.search(r'href="/promo"[^>]*target="_blank"[^>]*rel="noopener noreferrer"', html)
    assert DRIVE not in html   # Drive の URL はページに出さず /promo 経由（押された回数を数える）
    guest = _client().get("/guide").get_data(as_text=True)
    assert "nav-btn-promo" not in guest


def test_no_button_when_not_configured(db):
    html = _client("free_member").get("/guide").get_data(as_text=True)
    assert "nav-btn-promo" not in html


def test_session_navigation_includes_promo_in_new_tab(db):
    _save(db, promo_url=DRIVE, promo_label="特典", promo_until="")
    data = _client("free_member").get("/api/session-navigation").get_json()
    promo = [i for i in data["items"] if i["class_name"] == "nav-btn-promo"]
    assert promo and promo[0]["href"] == "/promo" and promo[0]["new_tab"] is True
    assert not any(i["label"] == "特典設定" for i in data["items"])   # 設定は管理者だけ


def test_promo_redirects_members_only(db):
    _save(db, promo_url=DRIVE, promo_label="特典", promo_until="")
    r = _client("free_member").get("/promo")
    assert r.status_code == 302 and r.headers["Location"] == DRIVE
    r = _client().get("/promo")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_promo_404_when_off(db):
    assert _client("free_member").get("/promo").status_code == 404


# ---- 管理画面 ----
def test_admin_page_is_admin_only(db):
    assert _client("free_member").get("/admin/promo").status_code == 403
    assert _client().get("/admin/promo").status_code == 302


def test_admin_saves_and_button_appears(db):
    admin = _client("admin")
    r = admin.post("/admin/promo", data={"csrf_token": "tok", "url": DRIVE, "label": "検証リスト", "until": "2099-12-31"})
    assert r.status_code == 200 and "保存しました" in r.get_data(as_text=True)
    assert promo_bp.current_promo()["label"] == "検証リスト"
    html = _client("free_member").get("/guide").get_data(as_text=True)
    assert "検証リスト" in html
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT updated_by FROM site_settings WHERE key='promo_url'").fetchone()[0] == "admin@example.com"


def test_admin_rejects_bad_link_and_keeps_input(db):
    r = _client("admin").post("/admin/promo", data={"csrf_token": "tok", "url": "https://example.com/x", "label": "", "until": ""})
    body = r.get_data(as_text=True)
    assert "Google Drive" in body and "https://example.com/x" in body
    assert promo_bp.current_promo() is None


def test_admin_post_requires_csrf(db):
    r = _client("admin").post("/admin/promo", data={"csrf_token": "wrong", "url": DRIVE})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "もう一度「保存する」" in body   # 素の Bad Request にしない
    assert DRIVE in body                                             # 入れた内容は残す
    assert promo_bp.current_promo() is None                          # 保存はしない


def test_admin_real_flow_uses_token_from_page(db):
    """画面を開いて、画面の確認コードで保存する（本物の流れ）。"""
    admin = _client("admin")
    with admin.session_transaction() as s:
        s.pop("csrf_token", None)
    html = admin.get("/admin/promo").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    r = admin.post("/admin/promo", data={"csrf_token": token, "url": DRIVE, "label": "特典", "until": ""})
    assert "保存しました" in r.get_data(as_text=True)
    assert promo_bp.current_promo()["url"] == DRIVE


def test_save_failure_shows_message(db, monkeypatch):
    def broken_save(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(promo_bp, "save_settings", broken_save)
    r = _client("admin").post("/admin/promo", data={"csrf_token": "tok", "url": DRIVE, "label": "", "until": ""})
    assert r.status_code == 200 and "保存できませんでした" in r.get_data(as_text=True)


def test_admin_can_turn_off(db):
    _save(db, promo_url=DRIVE, promo_label="特典", promo_until="")
    r = _client("admin").post("/admin/promo", data={"csrf_token": "tok", "url": "", "label": "", "until": ""})
    assert "止めました" in r.get_data(as_text=True)
    assert promo_bp.current_promo() is None


def test_reading_does_not_create_table(tmp_path, monkeypatch):
    """メニューを描くだけでは DB に表を作らない（書き込みは管理画面だけ）。"""
    path = tmp_path / "empty.db"
    monkeypatch.setattr(promo_bp, "_connect", lambda: sqlite3.connect(path))
    promo_bp.clear_cache()
    assert promo_bp.current_promo() is None
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='site_settings'").fetchall() == []
    promo_bp.clear_cache()


def test_presses_are_counted_per_day_and_shown_to_admin(db):
    _save(db, promo_url=DRIVE, promo_label="特典", promo_until="")
    member = _client("free_member")
    for _ in range(3):
        assert member.get("/promo").status_code == 302
    with sqlite3.connect(db) as conn:
        assert promo_bp.click_summary(conn) == {"today": 3, "week": 3, "total": 3}
        conn.execute("INSERT INTO promo_clicks (click_date, clicks) VALUES ('2000-01-01', 5)")
        assert promo_bp.click_summary(conn) == {"today": 3, "week": 3, "total": 8}
    body = _client("admin").get("/admin/promo").get_data(as_text=True)
    assert "今日 <b>3</b>" in body and "累計 <b>8</b>" in body


def test_guest_and_off_state_are_not_counted(db):
    _client().get("/promo")                       # ログイン前は数えない
    _client("free_member").get("/promo")          # 特典が止まっている間も数えない (404)
    with sqlite3.connect(db) as conn:
        assert promo_bp.click_summary(conn)["total"] == 0


def test_count_failure_still_sends_to_drive(db, monkeypatch):
    _save(db, promo_url=DRIVE, promo_label="特典", promo_until="")
    promo_bp.current_promo()                      # 設定は覚えた状態にしてから DB を壊す
    monkeypatch.setattr(promo_bp, "_connect", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    r = _client("free_member").get("/promo")
    assert r.status_code == 302 and r.headers["Location"] == DRIVE


def test_expiry_uses_japan_date(db, monkeypatch):
    _save(db, promo_url=DRIVE, promo_label="特典", promo_until="2026-10-31")
    monkeypatch.setattr(promo_bp, "today_jst", lambda: date(2026, 11, 1))
    assert promo_bp.current_promo() is None
