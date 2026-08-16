from datetime import date

import pytest

import scripts.fetch_missing_results as fetcher


@pytest.fixture(autouse=True)
def _block_real_download(monkeypatch):
    def unexpected_download(*_args, **_kwargs):
        raise AssertionError("test attempted an unmocked download")

    monkeypatch.setattr(fetcher.official_dl, "download_lzh", unexpected_download)


def _touch(path):
    path.write_bytes(b"present")


def test_find_missing_accepts_txt_or_lzh(tmp_path):
    _touch(tmp_path / "K250701.TXT")
    _touch(tmp_path / "k250702.lzh")

    assert fetcher.find_missing_dates(
        tmp_path, date(2025, 7, 1), date(2025, 7, 3)
    ) == [date(2025, 7, 3)]


def test_existing_date_is_never_downloaded(tmp_path, monkeypatch):
    _touch(tmp_path / "K250701.TXT")
    calls = []
    monkeypatch.setattr(
        fetcher.official_dl,
        "download_lzh",
        lambda kind, target_date: calls.append((kind, target_date)),
    )

    summary = fetcher.fetch_dates([date(2025, 7, 1)], tmp_path)

    assert calls == []
    assert summary["skip_existing"] == 1


def test_not_found_continues_and_is_counted(tmp_path, monkeypatch):
    calls = []

    def fake_download(kind, target_date):
        calls.append((kind, target_date))
        if target_date == date(2025, 7, 1):
            return None
        return tmp_path / f"k{target_date:%y%m%d}.lzh"

    monkeypatch.setattr(fetcher.official_dl, "download_lzh", fake_download)

    summary = fetcher.fetch_dates(
        [date(2025, 7, 1), date(2025, 7, 2)], tmp_path
    )

    assert calls == [
        ("K", date(2025, 7, 1)),
        ("K", date(2025, 7, 2)),
    ]
    assert summary["not_found"] == 1
    assert summary["ok"] == 1
    assert summary["requested"] == 2


def test_limit_caps_downloads(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        fetcher.official_dl,
        "download_lzh",
        lambda kind, target_date: calls.append((kind, target_date)) or tmp_path / "x.lzh",
    )

    summary = fetcher.fetch_dates(
        [date(2025, 7, 1), date(2025, 7, 2), date(2025, 7, 3)],
        tmp_path,
        limit=2,
    )

    assert calls == [("K", date(2025, 7, 1)), ("K", date(2025, 7, 2))]
    assert summary["requested"] == 2


def test_scan_never_calls_downloader(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(fetcher.config, "OFFICIAL_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(
        fetcher.official_dl,
        "download_lzh",
        lambda *args: calls.append(args),
    )

    assert fetcher.main(
        ["--scan", "--from", "2025-07-01", "--to", "2025-07-02"]
    ) == 0
    assert calls == []


def test_downloads_are_processed_in_date_order(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        fetcher.official_dl,
        "download_lzh",
        lambda kind, target_date: calls.append(target_date) or tmp_path / "x.lzh",
    )

    fetcher.fetch_dates(
        [date(2025, 7, 3), date(2025, 7, 1), date(2025, 7, 2)], tmp_path
    )

    assert calls == [date(2025, 7, 1), date(2025, 7, 2), date(2025, 7, 3)]


def test_unexpected_error_continues_and_is_grouped_by_reason(tmp_path, monkeypatch):
    calls = []

    def fake_download(_kind, target_date):
        calls.append(target_date)
        if target_date == date(2025, 7, 1):
            raise OSError("synthetic failure")
        return tmp_path / "x.lzh"

    monkeypatch.setattr(fetcher.official_dl, "download_lzh", fake_download)

    summary = fetcher.fetch_dates(
        [date(2025, 7, 1), date(2025, 7, 2)], tmp_path
    )

    assert calls == [date(2025, 7, 1), date(2025, 7, 2)]
    assert summary["error"] == 1
    assert summary["ok"] == 1
    assert summary["unavailable_reasons"] == {"OSError": 1}
