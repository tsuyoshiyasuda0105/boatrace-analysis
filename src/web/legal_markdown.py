"""草案 markdown を法定ページ用の安全な HTML に変換する。

草案は事業者本人と専門家の確認後に公開する前提のため、`[要入力]` 等の
プレースホルダーが残ったまま表示されないよう、環境変数で埋められる値は
埋め、それ以外は目立つ「未確定」バッジで囲む。html は最低限のタグしか
生成せず、markdown 側の生 HTML はそのまま出力しない。
"""
from __future__ import annotations

import html
import os
import re
from pathlib import Path
from typing import Iterable, Mapping

# 環境変数が未設定でも文書に埋めておく既定値
# (旧テンプレの見え方を維持する目的。事業者本人が確定させたら環境変数側で上書きする)

_DRAFT_DEFAULTS: Mapping[str, str] = {
    "LEGAL_PRICE": "月額1,380円（税込）",
    "LEGAL_SERVICE_NAME": "競艇｜バックテストLAB",
}


# 草案側のプレースホルダー -> 環境変数名
_DRAFT_ENV_MAP: Mapping[str, str] = {
    "[サービス正式名称]": "LEGAL_SERVICE_NAME",
    "[事業者名]": "LEGAL_OPERATOR_NAME",
    "[法人名又は個人事業者名を要入力]": "LEGAL_OPERATOR_NAME",
    "[氏名を要入力]": "LEGAL_RESPONSIBLE_PERSON",
    "[郵便番号、都道府県、市区町村、番地、建物名を要入力]": "LEGAL_ADDRESS",
    "[正式名称を要入力]": "LEGAL_SERVICE_NAME",
    "[月額○円（税込）／年額○円（税込）等を要入力]": "LEGAL_PRICE",
    "[毎月又は毎年の契約応当日]": "LEGAL_BILLING_ANCHOR",
    "[期限を要入力]": "LEGAL_CANCEL_DEADLINE",

    "[代表者又は運営統括責任者]": "LEGAL_RESPONSIBLE_PERSON",
    "[所在地]": "LEGAL_ADDRESS",
    "[電話番号]": "LEGAL_PHONE",
    "[メールアドレス]": "LEGAL_EMAIL",
    "[問い合わせ先]": "LEGAL_EMAIL",
    "[問い合わせ用メールアドレスを要入力]": "LEGAL_EMAIL",
    "[プラン名]": "LEGAL_PLAN_NAME",
    "[○○円]": "LEGAL_PRICE",
    "[利用可能期間を要入力]": "LEGAL_SERVICE_PERIOD",
    "[利用可能な機能、検索回数、保存件数、Discord参加権限等を要入力]": "LEGAL_PLAN_FEATURES",
    "[定期メンテナンス時間を要入力。現行案: 毎日4:00から7:00まで]": "LEGAL_MAINTENANCE_WINDOW",
    "[実施しない／期間、対象、終了日、終了後の料金及び自動課金条件を要入力]": "LEGAL_FREE_TRIAL",
    "[原則返金なし、重複請求、当方都合の長期停止、法令上必要な場合等、確定方針を要入力]": "LEGAL_REFUND_POLICY",
    "[事業者所在地を管轄する適切な地方裁判所又は簡易裁判所を要入力]": "LEGAL_JURISDICTION",
    "[実際に利用する解析サービスを要入力]": "LEGAL_ANALYTICS_VENDORS",
    "[利用する外部事業者と保存国]": "LEGAL_EXTERNAL_VENDORS",
    "[データ保存期間]": "LEGAL_RETENTION_PERIOD",
    "[制定日、施行日、最終改定日]": "LEGAL_EFFECTIVE_DATE",
}


def _placeholder_pattern() -> re.Pattern[str]:
    # `[ ... ]` を全部拾う。空欄「[ ]」も対象。
    return re.compile(r"\[[^\[\]\n]{1,80}\]")


def _fill_placeholders(text: str) -> tuple[str, list[str]]:
    """プレースホルダーを埋め、埋まらなかったものを返す。"""
    unfilled: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        env_name = _DRAFT_ENV_MAP.get(token)
        if env_name:
            value = os.environ.get(env_name, "").strip() or _DRAFT_DEFAULTS.get(env_name, "").strip()
            if value:
                return html.escape(value)
        if token not in unfilled:
            unfilled.append(token)
        return f'<mark class="legal-placeholder">{html.escape(token)}</mark>'

    return _placeholder_pattern().sub(_replace, text), unfilled


def _inline(text: str) -> str:
    """行内のリンクと強調のみ扱う (それ以外は素通し、生 HTML は escape 済み前提)。"""
    # ** 強調 **
    text = re.sub(r"\*\*([^*\n]+)\*\*", r"<strong>\1</strong>", text)
    # [表示](url) 形式のリンク (プレースホルダー除去のため既に置換済み)
    text = re.sub(
        r"(?<!\])\[([^\]\n]{1,80})\]\((https?://[^)\s]{1,300})\)",
        lambda m: f'<a href="{html.escape(m.group(2))}" rel="noopener nofollow" target="_blank">{m.group(1)}</a>',
        text,
    )
    return text


def _escape_and_inline(text: str) -> str:
    # プレースホルダー置換で <mark> が入っているので、既存タグを壊さない escape が要る。
    # 手順: (1) タグを退避 (2) escape (3) タグを復元 (4) inline 装飾
    tokens: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        tokens.append(match.group(0))
        return f"\x00{len(tokens) - 1}\x00"

    stashed = re.sub(r"<mark class=\"legal-placeholder\">[^<]+</mark>|<a [^>]+>[^<]+</a>", _stash, text)
    escaped = html.escape(stashed)
    for i, token in enumerate(tokens):
        escaped = escaped.replace(f"\x00{i}\x00", token)
    return _inline(escaped)


def markdown_to_html(source: str) -> tuple[str, list[str]]:
    """草案 markdown を法定ページ用の HTML に変換し、未確定項目の一覧を返す。

    サポートするのは実際の草案で使われている: 見出し (# ##)、順序無しリスト (- ), 順序付きリスト (1.),
    段落、`|` の表、行内 `**強調**` とリンクのみ。それ以外は素直に段落として扱う。
    """
    filled, unfilled = _fill_placeholders(source)
    lines = filled.split("\n")
    out: list[str] = []
    i = 0

    def _flush_paragraph(buf: list[str]) -> None:
        if not buf:
            return
        text = " ".join(s.strip() for s in buf if s.strip())
        if text:
            out.append(f"<p>{_escape_and_inline(text)}</p>")

    para: list[str] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # 見出し
        m_h = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if m_h:
            _flush_paragraph(para); para = []
            level = len(m_h.group(1))
            out.append(f"<h{level}>{_escape_and_inline(m_h.group(2).strip())}</h{level}>")
            i += 1
            continue
        # 表
        if stripped.startswith("|") and stripped.endswith("|") and "|" in stripped[1:-1]:
            _flush_paragraph(para); para = []
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                row = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                rows.append(row)
                i += 1
            if len(rows) >= 2 and all(re.fullmatch(r":?-{2,}:?", c) for c in rows[1]):
                header, body = rows[0], rows[2:]
            else:
                header, body = rows[0], rows[1:]
            out.append('<table class="legal-table"><thead><tr>')
            out.extend(f"<th>{_escape_and_inline(c)}</th>" for c in header)
            out.append("</tr></thead><tbody>")
            for row in body:
                out.append("<tr>")
                out.extend(f"<td>{_escape_and_inline(c)}</td>" for c in row)
                out.append("</tr>")
            out.append("</tbody></table>")
            continue
        # 順序無しリスト
        if re.match(r"^\s*[-*]\s+", line):
            _flush_paragraph(para); para = []
            items: list[str] = []
            while i < len(lines) and re.match(r"^\s*[-*]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*]\s+", "", lines[i]))
                i += 1
            out.append("<ul>")
            out.extend(f"<li>{_escape_and_inline(item.rstrip())}</li>" for item in items)
            out.append("</ul>")
            continue
        # 順序付きリスト
        if re.match(r"^\s*\d+\.\s+", line):
            _flush_paragraph(para); para = []
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            out.append("<ol>")
            out.extend(f"<li>{_escape_and_inline(item.rstrip())}</li>" for item in items)
            out.append("</ol>")
            continue
        # 空行 = 段落区切り
        if not stripped:
            _flush_paragraph(para); para = []
            i += 1
            continue
        # 通常の段落
        para.append(line)
        i += 1
    _flush_paragraph(para)
    return "\n".join(out), unfilled


def render_legal_draft(name: str) -> tuple[str, list[str]]:
    """`legal_drafts/<name>.md` を HTML に変換し、未確定項目を返す。"""
    path = Path(__file__).parent / "legal_drafts" / f"{name}.md"
    return markdown_to_html(path.read_text(encoding="utf-8"))
