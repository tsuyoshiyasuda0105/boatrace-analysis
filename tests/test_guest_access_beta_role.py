from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import session

from src.web import app as web_app
from src.web import auth
from src.web.membership import ROLE_RANK, normalize_role, role_allows


RACE_ID = "20260818-01-01"
TARGET_DATE = "2026-08-18"


def _snapshot() -> dict:
    return {
        "version": web_app.TOP_PAGE_SNAPSHOT_VERSION,
        "date": TARGET_DATE,
        "generated_at": "2026-08-18T09:00:00+09:00",
        "stadium_groups": [
            {
                "stadium_number": 1,
                "stadium_name": "桐生",
                "environment": {},
                "races": [
                    {
                        "race_id": RACE_ID,
                        "race_date": TARGET_DATE,
                        "race_number": 1,
                        "race_closed_at": "2026-08-18 11:00:00",
                        "stadium_number": 1,
                        "stadium_name": "桐生",
                        "results_count": 0,
                    }
                ],
            }
        ],
        # Deliberately hostile member-only content: guest rendering must drop it.
        "initial_market_signals": {
            "date": TARGET_DATE,
            "signals": {RACE_ID: {"label": "採用ROI戦略"}},
            "race_badges": {RACE_ID: {"market": {"label": "EV+"}}},
            "accident_watch": {},
        },
        "empty": False,
    }


def _create_app(monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("BOATRACE_TASK_TRIGGER", raising=False)
    monkeypatch.setenv("BOATRACE_GUEST_ACCESS", "1")
    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: TARGET_DATE)
    web_app.invalidate_cache()
    app = web_app.create_app(cached_predictions_only=True)
    app.config.update(TESTING=True, SECRET_KEY="guest-beta-role-test")
    return app


def _set_role(client, role: str) -> None:
    with client.session_transaction() as sess:
        sess["is_member"] = role in {
            "free_member",
            "beta_member",
            "paid_member",
            "admin",
        }
        sess["role"] = role
        sess["auth_provider"] = "test"


def test_beta_role_is_normalized_and_all_session_setters_treat_it_as_member(
    monkeypatch,
):
    app = _create_app(monkeypatch)

    assert ROLE_RANK == {
        "guest": 0,
        "free_member": 10,
        "beta_member": 15,
        "paid_member": 20,
        "admin": 100,
    }
    assert normalize_role("beta_member") == "beta_member"
    assert role_allows("beta_member", "free_member") is True
    assert role_allows("beta_member", "paid_member") is False
    assert "beta_member" in auth._SUPABASE_MEMBER_ROLES

    with app.test_request_context("/"):
        auth._set_supabase_session("beta-user", "beta@example.com", "beta_member")
        assert session["is_member"] is True
        assert session["role"] == "beta_member"

    with app.test_request_context("/"):
        auth._set_test_session_role("beta_member")
        assert session["is_member"] is True
        assert session["role"] == "beta_member"


def test_guest_public_pages_are_200_cache_only_and_hide_member_content(monkeypatch):
    app = _create_app(monkeypatch)
    snapshot = _snapshot()
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(
        web_app,
        "_races_for_date",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("guest TOP must not scan races")
        ),
    )
    monkeypatch.setattr(
        web_app,
        "_race_basic_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("guest detail cache miss must not build live data")
        ),
    )
    cache_reads: list[str] = []
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache",
        lambda key, *_args: cache_reads.append(f"fresh:{key}") or None,
    )
    monkeypatch.setattr(
        web_app,
        "_read_page_html_cache_stale",
        lambda key: cache_reads.append(f"stale:{key}") or None,
    )
    monkeypatch.setenv("BOATRACE_ALLOW_EXPENSIVE_WEB_RECOMPUTE", "1")
    client = app.test_client()

    root = client.get("/")
    races = client.get(f"/races?date={TARGET_DATE}")
    detail = client.get(f"/race/{RACE_ID}?recompute=1")

    for response in (root, races, detail):
        assert response.status_code == 200
    top_html = root.get_data(as_text=True)
    assert "桐生" in top_html
    assert 'aria-label="会員メニュー"' not in top_html
    assert "/member/today-races" not in top_html
    assert "ROIが高いレース候補" not in top_html
    assert "採用ROI戦略" not in top_html
    assert "EV+" not in top_html
    assert "Phase 2 採用" not in top_html
    assert "レース詳細を準備しています" in detail.get_data(as_text=True)
    assert any(item.startswith("fresh:") for item in cache_reads)
    assert any(item.startswith("stale:") for item in cache_reads)


def test_guest_receives_display_tags_but_not_member_judgment(monkeypatch):
    """一覧の表示タグ (事故/逃げ/エースモーター/進入変更/決まり手) はゲストにも
    渡すが、market(EV+)/signals などの会員限定キーは許可リストで落とす。"""
    app = _create_app(monkeypatch)
    snapshot = _snapshot()
    snapshot["initial_market_signals"]["race_badges"] = {
        RACE_ID: {
            "accident": {"label": "GUEST_ACCIDENT_0P85", "boats": [1]},
            "entry_change": {"label": "GUEST_ENTRY_CHANGE", "boats": [2]},
            "market": {"label": "EV+"},  # 会員限定: 落とすべき
        }
    }
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: snapshot)
    top_html = app.test_client().get(f"/races?date={TARGET_DATE}").get_data(as_text=True)

    # 表示タグは渡る
    assert "GUEST_ACCIDENT_0P85" in top_html
    assert "GUEST_ENTRY_CHANGE" in top_html
    # 会員限定の判断は漏れない
    assert "EV+" not in top_html
    assert "採用ROI戦略" not in top_html
    # アクセス解析ビーコンはゲスト (直接描画経路) にも載る
    assert "static.cloudflareinsights.com/beacon" in top_html


def test_guest_top_cache_miss_returns_immediately_without_live_load(monkeypatch):
    app = _create_app(monkeypatch)
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: None)
    monkeypatch.setattr(
        web_app,
        "db_connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("guest TOP cache miss must not open the live DB path")
        ),
    )

    response = app.test_client().get(f"/races?date={TARGET_DATE}")

    assert response.status_code == 200
    assert response.headers["Retry-After"] == "30"
    assert "ただいま混み合っています" in response.get_data(as_text=True)


def test_guest_kill_switch_is_evaluated_on_each_request(monkeypatch):
    app = _create_app(monkeypatch)
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: _snapshot())
    client = app.test_client()

    assert client.get("/").status_code == 200
    monkeypatch.setenv("BOATRACE_GUEST_ACCESS", "0")
    for path in ("/", f"/races?date={TARGET_DATE}", f"/race/{RACE_ID}"):
        response = client.get(path)
        assert response.status_code == 302
        assert "/login?next=" in response.headers["Location"]
    monkeypatch.setenv("BOATRACE_GUEST_ACCESS", "1")
    assert client.get("/").status_code == 200


def test_member_only_and_admin_pages_remain_closed_to_guests_and_beta(monkeypatch):
    app = _create_app(monkeypatch)
    guest = app.test_client()

    for path in ("/member/today-races", "/kachisuji/", "/public/roi"):
        assert guest.get(path).status_code in {302, 403}

    beta = app.test_client()
    _set_role(beta, "beta_member")
    assert beta.get("/kachisuji/").status_code == 200
    assert beta.get("/public/roi").status_code == 403

    free = app.test_client()
    _set_role(free, "free_member")
    assert free.get("/kachisuji/").status_code == 403


def test_public_backtest_flag_opens_backtest_to_free_members_only(monkeypatch, tmp_path: Path):
    """BOATRACE_PUBLIC_BACKTEST=1 のとき free_member もバックテスト可。
    ゲストは login_required で入れず、フラグ OFF なら free_member は 403 のまま。"""
    db_path = tmp_path / "kachisuji.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE racers (racer_number INTEGER, name TEXT, name_kana TEXT)"
        )
        connection.execute("INSERT INTO racers VALUES (4320, '峰竜太', 'ミネ リュウタ')")
    monkeypatch.setenv("KACHISUJI_DB", str(db_path))
    app = _create_app(monkeypatch)

    free = app.test_client()
    _set_role(free, "free_member")

    # フラグ OFF: free_member は従来どおり 403
    monkeypatch.delenv("BOATRACE_PUBLIC_BACKTEST", raising=False)
    assert free.get("/kachisuji/").status_code == 403
    assert free.get("/kachisuji/api/racers?q=峰").status_code == 403

    # フラグ ON: free_member は開放される (ページ・API とも)
    monkeypatch.setenv("BOATRACE_PUBLIC_BACKTEST", "1")
    assert free.get("/kachisuji/").status_code == 200
    assert free.get("/kachisuji/api/racers?q=峰").status_code == 200

    # フラグ ON でもゲスト (未ログイン) は login_required でリダイレクト＝登録動機を維持
    guest = app.test_client()
    assert guest.get("/kachisuji/").status_code in {302, 403}


def test_backtest_page_and_api_share_the_beta_permission(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "kachisuji.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE racers (racer_number INTEGER, name TEXT, name_kana TEXT)"
        )
        connection.execute(
            "INSERT INTO racers VALUES (4320, '峰竜太', 'ミネ リュウタ')"
        )
    monkeypatch.setenv("KACHISUJI_DB", str(db_path))
    app = _create_app(monkeypatch)

    beta = app.test_client()
    _set_role(beta, "beta_member")
    assert beta.get("/kachisuji/").status_code == 200
    assert beta.get("/kachisuji/api/racers?q=峰").status_code == 200

    free = app.test_client()
    _set_role(free, "free_member")
    assert free.get("/kachisuji/").status_code == 403
    assert free.get("/kachisuji/api/racers?q=峰").status_code == 403


def test_shared_race_detail_html_is_guest_safe_even_when_generated_by_member(
    monkeypatch,
):
    app = _create_app(monkeypatch)
    info = {
        "race_date": TARGET_DATE,
        "stadium_number": 1,
        "stadium_name": "桐生",
        "race_number": 1,
        "race_title": "",
        "race_subtitle": "",
        "race_closed_at": "2026-08-18 11:00:00",
        "boatcast_replay_url": "https://example.invalid/replay",
    }
    with app.test_request_context(f"/race/{RACE_ID}"):
        session["is_member"] = True
        session["role"] = "admin"
        html = app.jinja_env.get_template("race.html").render(
            info=info,
            preds=[],
            error=None,
            beforeinfo=None,
            venue_environment={},
            venue_warning={"level": "danger", "venue": "桐生", "msg": "ROI"},
            sweet_spot=True,
            actual_result=None,
            notice=None,
            trifecta_pw=[],
            trifecta_unified=[],
        )

    assert 'aria-label="会員メニュー"' not in html
    assert "/member/today-races" not in html
    assert "SWEET SPOT" not in html
    assert "ROI" not in html
    assert "EV+" not in html


def test_session_navigation_restores_member_race_link_without_guest_leak(monkeypatch):
    app = _create_app(monkeypatch)

    free = app.test_client()
    _set_role(free, "free_member")
    free_payload = free.get("/api/session-navigation").get_json()
    assert [item["label"] for item in free_payload["items"]] == [
        "バックテスト", "プラン申込",
    ]
    assert free_payload["home_url"] == "/member/today-races"

    paid = app.test_client()
    _set_role(paid, "paid_member")
    paid_payload = paid.get("/api/session-navigation").get_json()
    assert paid_payload["items"][0]["label"] == "本日のレース"

    member = app.test_client()
    _set_role(member, "admin")
    member_response = member.get("/api/session-navigation")
    member_payload = member_response.get_json()

    assert member_response.status_code == 200
    assert "private" in member_response.headers["Cache-Control"]
    assert "no-store" in member_response.headers["Cache-Control"]
    assert member_response.headers["Vary"] == "Cookie"
    assert member_payload["is_member"] is True
    assert [item["label"] for item in member_payload["items"]] == [
        "本日のレース", "バックテスト", "プラン申込", "ROI", "月別推移",
        "健全度", "事故率", "展示精度", "管理",
    ]
    assert member_payload["items"][0]["href"] == "/member/today-races"

    guest_response = app.test_client().get("/api/session-navigation")
    guest_body = guest_response.get_data(as_text=True)
    assert guest_response.get_json() == {"is_member": False}
    assert "/member/today-races" not in guest_body
    assert "バックテスト" not in guest_body


def test_free_member_header_hides_only_today_races_button(monkeypatch):
    app = _create_app(monkeypatch)
    monkeypatch.setattr(web_app, "_read_top_page_snapshot", lambda *_args: _snapshot())

    free = app.test_client()
    _set_role(free, "free_member")
    free_html = free.get("/").get_data(as_text=True)
    assert "nav-btn nav-btn-today" not in free_html
    assert "バックテスト</span>" in free_html
    assert "プラン申込</span>" in free_html

    paid = app.test_client()
    _set_role(paid, "paid_member")
    paid_html = paid.get("/").get_data(as_text=True)
    assert "nav-btn nav-btn-today" in paid_html


def test_shared_cached_race_detail_hydrates_navigation_from_session_only(monkeypatch):
    app = _create_app(monkeypatch)
    with app.test_request_context(f"/race/{RACE_ID}"):
        session["is_member"] = True
        session["role"] = "admin"
        shared_html = app.jinja_env.get_template("race.html").render(
            info={
                "race_date": TARGET_DATE,
                "stadium_number": 1,
                "stadium_name": "桐生",
                "race_number": 1,
                "race_title": "",
                "race_subtitle": "",
                "race_closed_at": "2026-08-18 11:00:00",
                "boatcast_replay_url": "",
            },
            preds=[],
            error=None,
            beforeinfo=None,
            venue_environment={},
            venue_warning=None,
            sweet_spot=False,
            actual_result=None,
            notice=None,
            trifecta_pw=[],
            trifecta_unified=[],
        )
    assert 'data-endpoint="/api/session-navigation"' in shared_html
    assert "/member/today-races" not in shared_html

    monkeypatch.setattr(web_app, "_read_page_html_cache", lambda *_args: shared_html)
    monkeypatch.setattr(web_app, "_today_jst_iso", lambda: TARGET_DATE)
    member = app.test_client()
    _set_role(member, "paid_member")
    guest = app.test_client()

    assert member.get(f"/race/{RACE_ID}").get_data(as_text=True) == shared_html
    assert guest.get(f"/race/{RACE_ID}").get_data(as_text=True) == shared_html
    member_nav = member.get("/api/session-navigation").get_json()
    guest_nav = guest.get("/api/session-navigation").get_json()
    assert member_nav["items"][0]["href"] == "/member/today-races"
    assert guest_nav == {"is_member": False}
