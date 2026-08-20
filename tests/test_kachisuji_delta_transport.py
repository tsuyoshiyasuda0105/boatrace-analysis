# -*- coding: utf-8 -*-
"""Postgres 輸送 (kachisuji_delta_files) と slim 適用の回帰テスト。

2026-08-20: Storage 経由の step27 配送が鍵未配布で不稼働だったため、
DATABASE_URL だけで動く Postgres 輸送に置き換えた。その往復と、
web 内部エンドポイントの認証を固定する。
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from src.kachisuji import delta_transport as dt


def _make_delta(path: Path, race_date: str, race_ids: list[str]) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE asof_race_features (race_id TEXT PRIMARY KEY, race_date TEXT)"
    )
    conn.execute("CREATE TABLE racers (racer_number INTEGER PRIMARY KEY, name TEXT)")
    for rid in race_ids:
        conn.execute(
            "INSERT INTO asof_race_features VALUES (?, ?)", (rid, race_date)
        )
    conn.commit()
    conn.close()


def _make_slim(path: Path) -> None:
    _make_delta(path, "2026-08-18", ["20260818-01-01"])


def test_upload_fetch_roundtrip_and_idempotency(tmp_path):
    delta = tmp_path / "kachisuji_delta_20260819.db"
    _make_delta(delta, "2026-08-19", ["20260819-01-01"])
    conn = sqlite3.connect(tmp_path / "transport.db")

    info = dt.upload_delta_file(delta, conn=conn)
    assert info["name"] == "20260819.db"
    assert info["sha256"] == hashlib.sha256(delta.read_bytes()).hexdigest()

    # 再アップロードは冪等 (同一内容なら成功、内容が違えばエラー)
    assert dt.upload_delta_file(delta, conn=conn)["name"] == "20260819.db"

    pending = dt.fetch_pending_payloads(set(), conn=conn)
    assert [name for name, _ in pending] == ["20260819.db"]
    assert dt.fetch_pending_payloads({"20260819.db"}, conn=conn) == []
    conn.close()


def test_upload_rejects_conflicting_content(tmp_path):
    conn = sqlite3.connect(tmp_path / "transport.db")
    a = tmp_path / "kachisuji_delta_20260819.db"
    _make_delta(a, "2026-08-19", ["20260819-01-01"])
    dt.upload_delta_file(a, conn=conn)
    b = tmp_path / "sub" / "kachisuji_delta_20260819.db"
    b.parent.mkdir()
    _make_delta(b, "2026-08-19", ["20260819-99-99"])  # 同名・別内容
    with pytest.raises(ValueError, match="different sha256"):
        dt.upload_delta_file(b, conn=conn)
    conn.close()


def test_apply_pending_to_slim_applies_and_records(tmp_path):
    slim = tmp_path / "kachisuji_slim.db"
    _make_slim(slim)
    delta = tmp_path / "kachisuji_delta_20260819.db"
    _make_delta(delta, "2026-08-19", ["20260819-01-01", "20260819-01-02"])
    conn = sqlite3.connect(tmp_path / "transport.db")
    dt.upload_delta_file(delta, conn=conn)

    summary = dt.apply_pending_to_slim(slim, conn=conn)
    assert summary["applied_files"] == 1
    assert summary["asof_added"] == 2
    assert summary["latest_race_date"] == "2026-08-19"

    # 2回目は未適用なし (applied_deltas 記帳済み)
    summary2 = dt.apply_pending_to_slim(slim, conn=conn)
    assert summary2["applied_files"] == 0
    conn.close()
    assert not Path(str(slim) + ".bak").exists()


def test_apply_restores_backup_on_schema_mismatch(tmp_path):
    slim = tmp_path / "kachisuji_slim.db"
    _make_slim(slim)
    bad = tmp_path / "kachisuji_delta_20260819.db"
    conn_bad = sqlite3.connect(bad)
    conn_bad.execute("CREATE TABLE asof_race_features (race_id TEXT)")  # 列不足
    conn_bad.execute("CREATE TABLE racers (racer_number INTEGER)")
    conn_bad.commit()
    conn_bad.close()
    conn = sqlite3.connect(tmp_path / "transport.db")
    dt.upload_delta_file(bad, conn=conn)
    before = slim.read_bytes()
    with pytest.raises(ValueError, match="schema mismatch"):
        dt.apply_pending_to_slim(slim, conn=conn)
    conn.close()
    assert slim.read_bytes() == before  # バックアップ復元済み


def test_internal_token_derives_from_database_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://example/db")
    expected = hashlib.sha256(b"postgres://example/db").hexdigest()[:40]
    assert dt.internal_token() == expected
    monkeypatch.delenv("DATABASE_URL")
    with pytest.raises(RuntimeError):
        dt.internal_token()


def test_internal_endpoint_rejects_without_token(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://example/db")
    from src.web import app as web_app

    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    app = web_app.create_app(cached_predictions_only=True)
    client = app.test_client()

    assert client.post("/kachisuji/internal/apply-deltas").status_code == 403
    assert client.post(
        "/kachisuji/internal/apply-deltas",
        headers={"X-Internal-Token": "wrong"},
    ).status_code == 403


def test_internal_endpoint_applies_with_valid_token(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgres://example/db")
    slim = tmp_path / "slim.db"
    _make_slim(slim)
    monkeypatch.setenv("KACHISUJI_DB", str(slim))
    monkeypatch.setattr(
        dt, "fetch_pending_payloads", lambda applied, conn=None: []
    )
    from src.web import app as web_app

    monkeypatch.setattr(web_app, "_ensure_db_initialized", lambda: None)
    app = web_app.create_app(cached_predictions_only=True)
    client = app.test_client()
    resp = client.post(
        "/kachisuji/internal/apply-deltas",
        headers={"X-Internal-Token": dt.internal_token()},
    )
    assert resp.status_code == 200
    assert resp.get_json()["applied_files"] == 0


def test_pc_nightly_uses_pg_uploader():
    """夜間パイプラインが Postgres 輸送スクリプトを使うことの静的チェック。"""
    source = Path("scripts/pc_nightly_prepare.py").read_text(encoding="utf-8")
    assert "upload_kachisuji_delta_pg.py" in source
