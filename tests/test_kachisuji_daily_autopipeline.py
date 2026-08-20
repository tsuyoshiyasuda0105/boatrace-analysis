from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scripts import apply_kachisuji_deltas as apply_script
from scripts import pc_nightly_prepare as nightly
from scripts import upload_kachisuji_delta as upload_script


def _create_db(path: Path, *, race_id: str, racer_id: int, race_date: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE asof_race_features (
                race_id TEXT PRIMARY KEY,
                race_date TEXT NOT NULL
            );
            CREATE TABLE racers (
                racer_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO asof_race_features VALUES (?, ?)",
            (race_id, race_date),
        )
        connection.execute("INSERT INTO racers VALUES (?, ?)", (racer_id, f"r{racer_id}"))
        connection.commit()
    finally:
        connection.close()


class _Response:
    def __init__(self, *, json_data=None, content=b"", status_code=200):
        self._json_data = json_data
        self.content = content
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    def __init__(self, *, list_rows=None, content=b""):
        self.list_rows = list_rows or []
        self.content = content
        self.posts = []
        self.gets = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if "/object/list/" in url:
            return _Response(json_data=self.list_rows)
        return _Response(status_code=200)

    def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return _Response(content=self.content)


def _counts(path: Path) -> tuple[int, int]:
    connection = sqlite3.connect(path)
    try:
        return (
            connection.execute("SELECT COUNT(*) FROM asof_race_features").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM racers").fetchone()[0],
        )
    finally:
        connection.close()


def test_upload_uses_dated_name_and_upsert_header(tmp_path: Path):
    delta = tmp_path / "kachisuji_delta_20260818.db"
    _create_db(delta, race_id="20260818-01-01", racer_id=2, race_date="2026-08-18")
    session = _Session()

    name = upload_script.upload_delta(
        delta,
        supabase_url="https://project.supabase.co/",
        service_key="test-key",
        session=session,
    )

    assert name == "20260818.db"
    url, kwargs = session.posts[0]
    assert url.endswith("/storage/v1/object/kachisuji-deltas/20260818.db")
    assert kwargs["headers"]["x-upsert"] == "true"
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
    assert delta.exists()


def test_storage_list_and_download_are_mockable(tmp_path: Path):
    session = _Session(
        list_rows=[{"name": "20260819.db"}, {"name": "ignore.txt"}],
        content=b"sqlite bytes",
    )
    storage = apply_script.SupabaseDeltaStorage(
        "https://project.supabase.co", "test-key", session=session
    )

    assert storage.list_names() == ["20260819.db"]
    destination = tmp_path / "20260819.db"
    storage.download("20260819.db", destination)

    assert destination.read_bytes() == b"sqlite bytes"
    assert "/object/authenticated/kachisuji-deltas/20260819.db" in session.gets[0][0]


def test_double_apply_is_idempotent_and_records_delta(tmp_path: Path):
    slim = tmp_path / "kachisuji_slim.db"
    delta = tmp_path / "20260818.db"
    _create_db(slim, race_id="20260817-01-01", racer_id=1, race_date="2026-08-17")
    _create_db(delta, race_id="20260818-01-01", racer_id=2, race_date="2026-08-18")

    first = apply_script.apply_delta_files(slim, [(delta.name, delta)])
    first_counts = _counts(slim)
    second = apply_script.apply_delta_files(slim, [(delta.name, delta)])

    assert first == {
        "applied_files": 1,
        "asof_added": 1,
        "racers_added": 1,
        "latest_race_date": "2026-08-18",
    }
    assert second["applied_files"] == 0
    assert _counts(slim) == first_counts == (2, 2)
    connection = sqlite3.connect(slim)
    try:
        assert connection.execute("SELECT name FROM applied_deltas").fetchall() == [
            ("20260818.db",)
        ]
    finally:
        connection.close()


def test_storage_pipeline_downloads_only_unapplied_names(tmp_path: Path):
    slim = tmp_path / "kachisuji_slim.db"
    old_delta = tmp_path / "20260818.db"
    new_delta = tmp_path / "20260819.db"
    _create_db(slim, race_id="base", racer_id=1, race_date="2026-08-17")
    _create_db(old_delta, race_id="old", racer_id=2, race_date="2026-08-18")
    _create_db(new_delta, race_id="new", racer_id=3, race_date="2026-08-19")
    apply_script.apply_delta_files(slim, [(old_delta.name, old_delta)])

    class FakeStorage:
        downloaded = []

        def list_names(self):
            return [old_delta.name, new_delta.name]

        def download(self, name, destination):
            self.downloaded.append(name)
            source = old_delta if name == old_delta.name else new_delta
            destination.write_bytes(source.read_bytes())

    storage = FakeStorage()
    result = apply_script.apply_storage_deltas(slim, storage)

    assert storage.downloaded == [new_delta.name]
    assert result["applied_files"] == 1
    assert result["latest_race_date"] == "2026-08-19"
    assert _counts(slim) == (3, 3)


def test_apply_main_connection_uses_uri_true(monkeypatch, tmp_path: Path):
    slim = tmp_path / "kachisuji_slim.db"
    delta = tmp_path / "20260818.db"
    _create_db(slim, race_id="old", racer_id=1, race_date="2026-08-17")
    _create_db(delta, race_id="new", racer_id=2, race_date="2026-08-18")
    real_connect = apply_script.sqlite3.connect
    calls = []

    def recording_connect(database, *args, **kwargs):
        calls.append((str(database), kwargs.copy()))
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(apply_script.sqlite3, "connect", recording_connect)
    apply_script.apply_delta_files(slim, [(delta.name, delta)])

    main_calls = [kwargs for database, kwargs in calls if database == str(slim.resolve())]
    assert main_calls
    assert main_calls[0]["uri"] is True


def test_corrupt_delta_restores_backup_without_changing_slim(tmp_path: Path):
    slim = tmp_path / "kachisuji_slim.db"
    corrupt = tmp_path / "20260818.db"
    _create_db(slim, race_id="old", racer_id=1, race_date="2026-08-17")
    before = slim.read_bytes()
    corrupt.write_bytes(b"not a sqlite database")

    with pytest.raises(sqlite3.DatabaseError):
        apply_script.apply_delta_files(slim, [(corrupt.name, corrupt)])

    assert slim.read_bytes() == before
    assert Path(str(slim) + ".bak").read_bytes() == before
    assert _counts(slim) == (1, 1)
    connection = sqlite3.connect(slim)
    try:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='applied_deltas'"
        ).fetchone() is None
    finally:
        connection.close()


def test_nightly_delta_failure_does_not_change_existing_success(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(args, allow_prod_sync=False):
        calls.append((args, allow_prod_sync))
        return args[:1] != ["scripts/upload_kachisuji_delta_pg.py"]

    monkeypatch.setattr(nightly, "_run_local", fake_run)
    monkeypatch.setattr(nightly, "ROOT", tmp_path)
    monkeypatch.setattr(nightly, "_completed_date", lambda: "2026-08-18")
    monkeypatch.setattr(
        nightly,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "date": "2026-08-19",
                "sync_start": None,
                "sync_end": None,
                "skip_sync": True,
            },
        )(),
    )

    assert nightly.main() == 0
    assert calls[-2][0][0:3] == [
        "scripts/refresh_kachisuji_daily.py",
        "--date",
        "2026-08-18",
    ]
    assert calls[-1][0][0] == "scripts/upload_kachisuji_delta_pg.py"
    assert calls[-1][1] is True


def test_nightly_rerun_reuses_retained_delta(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(nightly, "ROOT", tmp_path)
    delta = tmp_path / "data" / "kachisuji_delta_20260818.db"
    delta.parent.mkdir()
    delta.write_bytes(b"retained")
    calls = []
    monkeypatch.setattr(
        nightly,
        "_run_local",
        lambda args, allow_prod_sync=False: calls.append((args, allow_prod_sync)) or True,
    )

    assert nightly._run_kachisuji_daily("2026-08-18") is True
    assert [call[0][0] for call in calls] == ["scripts/upload_kachisuji_delta_pg.py"]


def test_completed_date_is_previous_day_in_jst():
    from datetime import datetime, timezone

    # 16:30 UTC is already 01:30 on the next calendar day in JST.
    assert nightly._completed_date(datetime(2026, 8, 18, 16, 30, tzinfo=timezone.utc)) == "2026-08-18"
