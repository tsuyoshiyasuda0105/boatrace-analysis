import os
import sys
import types
from pathlib import Path

os.environ["DATABASE_URL"] = ""

from src.collectors import official_dl


class _Member:
    filename = "B260731.TXT"


class _Archive:
    def __init__(self, path):
        self.path = path

    def infolist(self):
        return [_Member()]

    def read(self, filename):
        assert filename == "B260731.TXT"
        return b"official-data"


def test_extract_txt_uses_python_lzh_reader(tmp_path, monkeypatch):
    archive_path = tmp_path / "b260731.lzh"
    archive_path.write_bytes(b"fake-lzh")
    monkeypatch.setitem(sys.modules, "lhafile", types.SimpleNamespace(Lhafile=_Archive))
    monkeypatch.setattr(official_dl.shutil, "which", lambda _: None)

    result = official_dl.extract_txt(archive_path)

    assert result == tmp_path / "B260731.TXT"
    assert result.read_bytes() == b"official-data"


def test_extract_txt_reuses_existing_nonempty_file(tmp_path, monkeypatch):
    archive_path = tmp_path / "b260731.lzh"
    archive_path.write_bytes(b"fake-lzh")
    expected = tmp_path / "B260731.TXT"
    expected.write_bytes(b"already-extracted")

    def fail_import(*args, **kwargs):
        raise AssertionError("archive should not be reopened")

    monkeypatch.setitem(
        sys.modules,
        "lhafile",
        types.SimpleNamespace(Lhafile=fail_import),
    )

    assert official_dl.extract_txt(archive_path) == expected
