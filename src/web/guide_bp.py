"""使い方ページ（誰でも閲覧可）。

解説動画は YouTube 検索から人を集めるためのものではなく、
「もう触る気がある人」がアプリの中で見るための教材なので、ここに置く。
動画の実体は YouTube に残したまま、埋め込みで見せる。

順番は学習順。上から見れば一通り使えるようになる並びにしてある。
動画を足すときは GUIDE_VIDEOS に 1 件足すだけでよい。
"""
from __future__ import annotations

from flask import Blueprint, render_template

bp = Blueprint("guide", __name__)

# YouTube の動画ID。プライバシー保護のため youtube-nocookie ドメインで埋め込む
# (CSP の frame-src もそのドメインだけを許可している)。
#
# ⚠ 年齢制限 (ytAgeRestricted) が付いた動画は、外部サイトに埋め込んでも
#    「YouTube でのみご視聴いただけます」と表示されて再生できない。
#    ここに足す前に必ず確認すること (2026-09-09 に u8BOGqaugqM で判明し、除外した)。
#    確認: youtube API の contentDetails.contentRating.ytRating を見る。
GUIDE_VIDEOS = [
    {
        "id": "AIxhNhc3tL8",
        "title": "バックテストLABとは",
        "lead": "株やFXにはあるのに競艇にはなかった「過去データでの検証」。何ができる道具なのかを、ひと通り見せます。",
        "length": "2分5秒",
    },
    {
        "id": "QQGrroWrELY",
        "title": "まず無料の全体画面を触ってみる",
        "lead": "イン逃げを狙うか、荒れるレースを狙うか。登録なしで使える画面から始めます。",
        "length": "1分47秒",
    },
    {
        "id": "1VwzDy0yzjk",
        "title": "その数字は信じていいのか",
        "lead": "回収率が高く出ても、そのまま信じてはいけない場合があります。数字の読み方を扱います。",
        "length": "3分",
    },
    {
        "id": "WW418AN_qW0",
        "title": "条件を足して研ぎ澄ます",
        "lead": "条件を足すと的は絞れますが、当てはまるレースは減ります。そのさじ加減の話です。",
        "length": "2分40秒",
    },
    {
        "id": "A5sP42UMXJ0",
        "title": "使用例：馬場貴也選手の3号艇を調べる",
        "lead": "実際に一つの問いを立てて、答えが出るまでを通しで見せます。",
        "length": "1分58秒",
    },
]


@bp.get("/guide")
def guide():
    return render_template("guide.html", videos=GUIDE_VIDEOS)
