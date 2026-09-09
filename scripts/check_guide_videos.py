# -*- coding: utf-8 -*-
"""使い方ページの動画が本当に埋め込めるかを確認する。

年齢制限 (ytAgeRestricted) が付いた動画は、外部サイトに埋め込んでも
「YouTube でのみご視聴いただけます」と出て再生できない。見た目には
プレイヤーの枠が出るので、実際に押してみるまで気づけない。

使い方:
    python scripts/check_guide_videos.py

動画を GUIDE_VIDEOS に足したら必ず実行すること。
認証は lecture-factory 側の youtube_token.json を使う（読み取りのみ）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, r"C:\boat_project\lecture-factory\tools")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from src.web.guide_bp import GUIDE_VIDEOS  # noqa: E402


def main() -> int:
    try:
        from youtube_auth import get_credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        print(f"NG: YouTube API の依存が見つかりません ({exc})")
        print("    lecture-factory 側の環境が要ります。")
        return 2

    yt = build("youtube", "v3", credentials=get_credentials())
    ids = [v["id"] for v in GUIDE_VIDEOS]
    found = {}
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="snippet,status,contentDetails",
                             id=",".join(ids[i:i + 50])).execute()
        for it in r["items"]:
            found[it["id"]] = it

    problems = []
    print(f"{'ID':13} {'判定':6} タイトル")
    print("-" * 66)
    for v in GUIDE_VIDEOS:
        it = found.get(v["id"])
        if not it:
            problems.append(f"{v['id']} ({v['title']}): 動画が見つかりません（削除された？）")
            print(f"{v['id']:13} {'NG':6} 見つかりません")
            continue
        rating = (it["contentDetails"].get("contentRating") or {}).get("ytRating")
        embeddable = it["status"].get("embeddable")
        privacy = it["status"]["privacyStatus"]
        bad = []
        if rating == "ytAgeRestricted":
            bad.append("年齢制限")
        if not embeddable:
            bad.append("埋め込み不可")
        if privacy == "private":
            bad.append("非公開")
        mark = "NG" if bad else "OK"
        print(f"{v['id']:13} {mark:6} {v['title'][:34]}" + (f"  ← {'/'.join(bad)}" if bad else ""))
        if bad:
            problems.append(f"{v['id']} ({v['title']}): {'/'.join(bad)}")

    if problems:
        print(f"\n{len(problems)} 件、使い方ページで再生できません:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"\nOK: {len(GUIDE_VIDEOS)}本すべて埋め込み再生できます。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
