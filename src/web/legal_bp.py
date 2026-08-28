"""Public legal disclosure pages for the paid service.

草案 markdown (src/web/legal_drafts/*.md) を安全な HTML に変換して配信する。
プレースホルダーで残っている項目は目立つ黄色で表示し、公開前に環境変数で
埋めるか、markdown を直接編集して確定させる想定。
"""
from __future__ import annotations

from flask import Blueprint, render_template

from src.web.legal_markdown import render_legal_draft

bp = Blueprint("legal", __name__, url_prefix="/legal")

TERMS_VERSION = "2026-08-28"


_ROUTES = {
    "terms":     ("APP_TERMS_DRAFT",              "利用規約"),
    "privacy":   ("PRIVACY_POLICY_DRAFT",         "プライバシーポリシー"),
    "tokushoho": ("TOKUSHOHO_AND_CHECKOUT_DRAFT", "特定商取引法に基づく表記"),
    "discord":   ("DISCORD_COMMUNITY_RULES_DRAFT", "Discord参加・投稿規約"),
}


def _render(name: str):
    draft, page_title = _ROUTES[name]
    body, unfilled = render_legal_draft(draft)
    return render_template(
        "legal_draft_shell.html",
        body=body,
        unfilled=unfilled,
        page_title=page_title,
    )


@bp.get("/terms")
def terms():
    return _render("terms")


@bp.get("/tokushoho")
def tokushoho():
    return _render("tokushoho")


@bp.get("/privacy")
def privacy():
    return _render("privacy")


@bp.get("/discord")
def discord():
    return _render("discord")
