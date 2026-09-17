"""はじめての人向けの案内ページ（/start・誰でも閲覧可）。

SNS（YouTube・X・TikTok・note）から来た人が最初に着地する場所。
レース一覧にいきなり落とすと「何のアプリか」が伝わらないので、ここで
「過去データで答え合わせする道具」だと一枚で分かるようにする (2026-09-17)。

DB には触らない。数字はページ内に固定で書いてあり、出典は記憶メモ
(omura-tide-wind / adjacent-matchup-and-boat4-makuri) と note 記事。
"""
from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("lp", __name__)

YOUTUBE_CHANNEL_URL = "https://www.youtube.com/channel/UCKJb4VZPy34vxqrFQAD015Q"


@bp.get("/start")
def start():
    return render_template("lp.html", youtube_url=YOUTUBE_CHANNEL_URL)
