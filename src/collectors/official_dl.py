"""
Layer 1: 公式ダウンロードファイル取得 (LZH)

URL パターン:
  番組表:   https://www1.mbrace.or.jp/od2/B/{YYYYMM}/b{YYMMDD}.lzh
  競走成績: https://www1.mbrace.or.jp/od2/K/{YYYYMM}/k{YYMMDD}.lzh

LZH 解凍は 7-Zip (C:\\Program Files\\7-Zip\\7z.exe) を subprocess 呼び出し。
解凍結果は Shift_JIS の固定幅テキスト。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)

SEVEN_ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")
DOWNLOAD_INTERVAL = 1.5  # 秒、サーバ負荷軽減


def _file_url(kind: str, target_date: date) -> str:
    """kind in ('B', 'K')"""
    yyyymm = target_date.strftime("%Y%m")
    yymmdd = target_date.strftime("%y%m%d")
    base = "https://www1.mbrace.or.jp/od2"
    return f"{base}/{kind}/{yyyymm}/{kind.lower()}{yymmdd}.lzh"


def _local_lzh_path(kind: str, target_date: date) -> Path:
    base = config.OFFICIAL_PROGRAMS_DIR if kind == "B" else config.OFFICIAL_RESULTS_DIR
    return base / f"{kind.lower()}{target_date.strftime('%y%m%d')}.lzh"


def _local_txt_path(kind: str, target_date: date) -> Path:
    base = config.OFFICIAL_PROGRAMS_DIR if kind == "B" else config.OFFICIAL_RESULTS_DIR
    return base / f"{kind.upper()}{target_date.strftime('%y%m%d')}.TXT"


def download_lzh(kind: str, target_date: date, force: bool = False) -> Optional[Path]:
    """LZH をダウンロードしローカル保存。404はNone、保存済みは再DLしない。"""
    config.ensure_dirs()
    out = _local_lzh_path(kind, target_date)
    if out.exists() and not force:
        return out
    url = _file_url(kind, target_date)
    try:
        resp = requests.get(
            url,
            timeout=config.REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": config.USER_AGENT},
        )
    except requests.RequestException as e:
        logger.warning("download error %s: %s", url, e)
        return None
    if resp.status_code == 404:
        logger.info("not found: %s", url)
        return None
    if resp.status_code != 200:
        logger.warning("HTTP %s: %s", resp.status_code, url)
        return None
    out.write_bytes(resp.content)
    return out


def extract_txt(lzh_path: Path) -> Optional[Path]:
    """7-Zip で LZH を解凍。同ディレクトリに *.TXT を出力。"""
    out_dir = lzh_path.parent
    base_name = lzh_path.stem
    expected = out_dir / f"{base_name.upper()}.TXT"
    if expected.exists() and expected.stat().st_size > 0:
        return expected

    try:
        import lhafile

        archive = lhafile.Lhafile(str(lzh_path))
        members = [
            info
            for info in archive.infolist()
            if Path(info.filename).suffix.lower() == ".txt"
        ]
        if members:
            member = next(
                (
                    info
                    for info in members
                    if Path(info.filename).stem.lower() == base_name.lower()
                ),
                members[0],
            )
            expected.write_bytes(archive.read(member.filename))
            if expected.stat().st_size > 0:
                return expected
    except Exception as exc:
        logger.warning("Python LZH extraction failed for %s: %s", lzh_path, exc)

    seven_zip = next(
        (
            candidate
            for candidate in (
                shutil.which("7zz"),
                shutil.which("7z"),
                shutil.which("7za"),
                str(SEVEN_ZIP) if SEVEN_ZIP.exists() else None,
            )
            if candidate
        ),
        None,
    )
    if not seven_zip:
        logger.warning("No LZH extractor is available for %s", lzh_path)
        return None
    # 7z e (extract without paths), -y (assume yes), -o (output dir)
    cmd = [seven_zip, "e", str(lzh_path), f"-o{out_dir}", "-y"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if result.returncode != 0:
        logger.warning("7z failed: %s", result.stderr or result.stdout)
        return None
    # 解凍されたファイルを探す (例: b240101.lzh → B240101.TXT)
    candidates = [
        out_dir / f"{base_name.upper()}.TXT",
        out_dir / f"{base_name}.TXT",
        out_dir / f"{base_name.upper()}.txt",
    ]
    for c in candidates:
        if c.exists():
            return c
    # フォールバック: out_dir 内の最新 TXT
    txts = sorted(out_dir.glob("*.TXT"), key=lambda p: p.stat().st_mtime, reverse=True)
    return txts[0] if txts else None


def fetch_one(kind: str, target_date: date, force: bool = False) -> Optional[Path]:
    """LZH ダウンロード → 解凍 までを実行。最終 TXT パスを返す。"""
    lzh = download_lzh(kind, target_date, force=force)
    if lzh is None:
        return None
    txt = extract_txt(lzh)
    return txt


def fetch_range(kind: str, start: date, end: date,
                interval_seconds: float = DOWNLOAD_INTERVAL,
                force: bool = False) -> dict:
    """[start, end] 範囲を順次ダウンロード。"""
    from datetime import timedelta
    summary = {"requested": 0, "downloaded": 0, "extracted": 0, "missing": 0}
    cur = start
    while cur <= end:
        summary["requested"] += 1
        lzh = _local_lzh_path(kind, cur)
        already = lzh.exists() and not force
        path = download_lzh(kind, cur, force=force)
        if path is None:
            summary["missing"] += 1
            cur = cur + timedelta(days=1)
            continue
        if not already:
            summary["downloaded"] += 1
            time.sleep(interval_seconds)
        txt = extract_txt(path)
        if txt:
            summary["extracted"] += 1
        cur = cur + timedelta(days=1)
    return summary
