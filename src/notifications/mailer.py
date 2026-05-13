"""
SMTP メール送信

対応 SMTP サーバー (環境変数で切り替え):
  - Gmail SMTP (smtp.gmail.com:587, アプリパスワード)
  - SendGrid (smtp.sendgrid.net:587)
  - Brevo (旧Sendinblue, smtp-relay.brevo.com:587)
  - Resend (smtp.resend.com:587)
  - 任意の SMTP

環境変数:
  BOATRACE_SMTP_HOST     - SMTP ホスト (例: smtp.gmail.com)
  BOATRACE_SMTP_PORT     - 587 (STARTTLS) または 465 (SSL)
  BOATRACE_SMTP_USER     - 認証ユーザー (メアド or API キー識別子)
  BOATRACE_SMTP_PASSWORD - 認証パスワード or API キー
  BOATRACE_SMTP_FROM     - From 表示 (例: "BOATRACE Alert <noreply@example.com>")
  BOATRACE_SITE_URL      - Web サイトの公開 URL (リンク生成用)

無設定時:
  → コンソールに出力するだけのスタブ動作 (開発時)
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, make_msgid


def _esc(value, default: str = "?") -> str:
    """HTML エスケープのヘルパー (None と非文字列も安全に処理)"""
    if value is None:
        return default
    return html.escape(str(value), quote=True)

logger = logging.getLogger(__name__)

DEFAULT_FROM = "BOATRACE Alert <noreply@boatrace-web.onrender.com>"
DEFAULT_SITE_URL = "https://boatrace-web.onrender.com"


def _get_config() -> dict:
    return {
        "host": os.environ.get("BOATRACE_SMTP_HOST", "").strip(),
        "port": int(os.environ.get("BOATRACE_SMTP_PORT", "587") or 587),
        "user": os.environ.get("BOATRACE_SMTP_USER", "").strip(),
        "password": os.environ.get("BOATRACE_SMTP_PASSWORD", ""),
        "from_addr": os.environ.get("BOATRACE_SMTP_FROM", DEFAULT_FROM),
        "site_url": os.environ.get("BOATRACE_SITE_URL", DEFAULT_SITE_URL).rstrip("/"),
        # HTTP API バックエンド (Render Free は SMTP outbound 不可なので必須)
        "brevo_api_key": os.environ.get("BOATRACE_BREVO_API_KEY", "").strip(),
        "resend_api_key": os.environ.get("BOATRACE_RESEND_API_KEY", "").strip(),
    }


def _smtp_configured() -> bool:
    c = _get_config()
    return bool(c["host"] and c["user"] and c["password"])


def _parse_from(from_addr: str) -> tuple[str, str]:
    """'Name <addr@example.com>' or 'addr@example.com' → (name, addr)"""
    m = re.match(r"^\s*(.*?)\s*<([^>]+)>\s*$", from_addr)
    if m:
        return m.group(1) or "", m.group(2)
    return "", from_addr.strip()


def _send_via_brevo_http(to: str, subject: str, body_text: str, body_html: str = None) -> bool:
    """Brevo (旧Sendinblue) Transactional Email API 経由で送信。
    Render Free Tier の SMTP outbound ブロックを回避するため HTTPS API を使う。
    https://developers.brevo.com/reference/sendtransacemail
    """
    cfg = _get_config()
    name, addr = _parse_from(cfg["from_addr"])
    payload = {
        "sender": {"email": addr, **({"name": name} if name else {})},
        "to": [{"email": to}],
        "subject": subject,
        "textContent": body_text,
    }
    if body_html:
        payload["htmlContent"] = body_html

    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "accept": "application/json",
            "content-type": "application/json",
            "api-key": cfg["brevo_api_key"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
            body = r.read().decode("utf-8", errors="replace")
        if 200 <= status < 300:
            logger.info("mail sent via Brevo to %s*** (subject=%s)", to[:5], subject[:40])
            return True
        logger.error("Brevo API non-2xx: %s %s", status, body[:300])
        return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        logger.error("Brevo HTTPError: %s %s body=%s", e.code, e.reason, body[:300])
        return False
    except Exception as e:
        logger.exception("Brevo send failed: %s", e)
        return False


def _send_via_resend_http(to: str, subject: str, body_text: str, body_html: str = None) -> bool:
    """Resend HTTP API 経由で送信。
    https://resend.com/docs/api-reference/emails/send-email
    """
    cfg = _get_config()
    payload = {
        "from": cfg["from_addr"],
        "to": [to],
        "subject": subject,
        "text": body_text,
    }
    if body_html:
        payload["html"] = body_html

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg['resend_api_key']}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status = r.status
            body = r.read().decode("utf-8", errors="replace")
        if 200 <= status < 300:
            logger.info("mail sent via Resend to %s*** (subject=%s)", to[:5], subject[:40])
            return True
        logger.error("Resend API non-2xx: %s %s", status, body[:300])
        return False
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        logger.error("Resend HTTPError: %s %s body=%s", e.code, e.reason, body[:300])
        return False
    except Exception as e:
        logger.exception("Resend send failed: %s", e)
        return False


def _send(to: str, subject: str, body_text: str, body_html: str = None):
    """メール送信本体。
    優先順位:
      1. BOATRACE_BREVO_API_KEY が設定されていれば Brevo HTTP API
      2. BOATRACE_RESEND_API_KEY が設定されていれば Resend HTTP API
      3. SMTP (ローカルや有料 PaaS 用)
      4. 未設定時はコンソール出力 (開発)
    Render Free Tier では SMTP outbound が遮断されているため HTTP API バックエンドが必須。
    """
    cfg = _get_config()

    # 1. Brevo HTTP API (Render Free 対応)
    if cfg["brevo_api_key"]:
        return _send_via_brevo_http(to, subject, body_text, body_html)

    # 2. Resend HTTP API (Render Free 対応)
    if cfg["resend_api_key"]:
        return _send_via_resend_http(to, subject, body_text, body_html)

    # 3. SMTP (ローカル / 有料 PaaS)
    msg = EmailMessage()
    msg["From"] = cfg["from_addr"]
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain="boatrace-web.onrender.com")
    msg.set_content(body_text, subtype="plain", charset="utf-8")
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    if not _smtp_configured():
        # 開発用: コンソールに出力
        logger.warning("=" * 60)
        logger.warning("メール送信バックエンド未設定: スキップ (内容をログ出力)")
        logger.warning("To: %s", to)
        logger.warning("Subject: %s", subject)
        logger.warning("---")
        logger.warning("\n%s", body_text)
        logger.warning("=" * 60)
        return False

    try:
        ctx = ssl.create_default_context()
        if cfg["port"] == 465:
            # SSL
            with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=ctx, timeout=30) as smtp:
                smtp.login(cfg["user"], cfg["password"])
                smtp.send_message(msg)
        else:
            # STARTTLS
            with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as smtp:
                smtp.ehlo()
                smtp.starttls(context=ctx)
                smtp.ehlo()
                smtp.login(cfg["user"], cfg["password"])
                smtp.send_message(msg)
        logger.info("mail sent to %s (subject=%s)", to[:5] + "***", subject[:40])
        return True
    except Exception as e:
        logger.exception("SMTP send failed to %s: %s", to[:5] + "***", e)
        return False


def send_verification_email(email: str, verify_token: str) -> bool:
    """確認メール"""
    site = _get_config()["site_url"]
    link = f"{site}/alerts/verify?token={verify_token}"
    subject = "[BOATRACE Alert] メール認証のお願い"
    text = f"""BOATRACE Alert への購読ありがとうございます。

以下のリンクをクリックして登録を完了してください (48時間以内):

{link}

このメールに心当たりが無い場合は無視してください (自動的に失効します)。

---
BOATRACE 予測 v0.8
{site}
"""
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:600px;margin:auto">
<h2>📬 BOATRACE Alert メール認証</h2>
<p>購読いただきありがとうございます。下のボタンをクリックして登録を完了してください (48時間以内有効)。</p>
<p style="text-align:center;margin:30px 0">
  <a href="{link}" style="display:inline-block;padding:12px 24px;background:#2563eb;color:#fff;text-decoration:none;border-radius:6px;font-weight:bold">
    認証する
  </a>
</p>
<p style="font-size:12px;color:#666">
  ボタンが押せない場合は以下を貼り付けてください:<br>
  <code style="word-break:break-all">{link}</code>
</p>
<hr>
<p style="font-size:12px;color:#999">
  心当たりが無い場合は無視してください。<br>
  BOATRACE 予測 v0.8 / <a href="{site}">{site}</a>
</p>
</body></html>"""
    return _send(email, subject, text, html)


def send_l4_alert(email: str, alerts: list[dict], unsubscribe_token: str) -> bool:
    """L4 マーク発火時のアラートメール。
    alerts は次の形式のリスト:
      {race_id, stadium_name, race_number, race_closed_at,
       label, recovery, bet, alert_type,
       rank, rank_label, rank_emoji, natl_1, local_1, racer_name}
    rank は "plus_plus" / "plus" / "base" のいずれか (省略時は base 扱い)。
    """
    if not alerts:
        return False
    site = _get_config()["site_url"]
    unsub = f"{site}/alerts/unsubscribe?token={unsubscribe_token}"

    # モード判定 (1件でも morning が混ざれば朝モード扱い)
    has_morning = any(a.get("mode") == "morning" for a in alerts)
    has_confirmed = any(a.get("mode") == "confirmed" or not a.get("mode") for a in alerts)
    is_morning_only = has_morning and not has_confirmed

    # ランク別に件数集計 (件名表示用)
    n_plus_plus = sum(1 for a in alerts if a.get("rank") == "plus_plus")
    n_plus = sum(1 for a in alerts if a.get("rank") == "plus")
    n_a2 = sum(1 for a in alerts if a.get("rank") == "a2")
    n_base = len(alerts) - n_plus_plus - n_plus - n_a2
    n = len(alerts)

    # 件名にランク分布を表示
    rank_parts = []
    if n_plus_plus: rank_parts.append(f"L4++ {n_plus_plus}")
    if n_plus:      rank_parts.append(f"L4+ {n_plus}")
    if n_base:      rank_parts.append(f"L4 {n_base}")
    if n_a2:        rank_parts.append(f"A2派生 {n_a2}")
    rank_summary = " / ".join(rank_parts)

    if is_morning_only:
        subject = f"[BOATRACE] 🌅 朝L4候補 {n}件 ({rank_summary}) - オッズ確定前予報"
    elif has_morning:
        subject = f"[BOATRACE] 🎯 L4 + 🌅朝候補 {n}件 ({rank_summary})"
    else:
        subject = f"[BOATRACE] 🎯 L4 シグナル {n}件 ({rank_summary})"

    # text 版
    header = "朝L4候補 (オッズ確定前予報)" if is_morning_only else "L4 シグナル"
    lines = [
        f"BOATRACE Alert: {header} {n} 件発火",
        f"  内訳: {rank_summary}",
        "",
    ]
    if is_morning_only:
        lines.extend([
            "※ 朝の予測モデルベースの候補です。",
            "  T-5min/T-15min オッズ確定後に再判定されます。",
            "  本命オッズが 500-1000円帯に入らなければ取り消されます。",
            "",
        ])
    lines.extend([
        "L4++ (🥇 国1%>=7 ∧ 局1%>=7) … 検証回収率 190.3%",
        "L4+  (🥈 国1%>=7)            … 検証回収率 188.2%",
        "L4   (⭐ 基本 A1)            … グレード別検証値",
        "",
        "対象レース一覧 (締切時刻順):",
        "",
    ])
    for a in sorted(alerts, key=lambda x: x.get("race_closed_at", "")):
        rk = a.get("rank_label", "L4")
        rec = a.get("recovery", 0) or 0
        natl = a.get("natl_1", 0) or 0
        local = a.get("local_1", 0) or 0
        racer = a.get("racer_name", "")
        morn_tag = " 🌅朝候補" if a.get("mode") == "morning" else ""
        lines.append(f"  ▶ [{rk}{morn_tag}] {a.get('race_closed_at','?')} | "
                     f"{a.get('stadium_name','?')} {a.get('race_number','?')}R | "
                     f"{a.get('label','')} ({rec:.1f}%)")
        if a.get("mode") == "morning":
            pf = a.get("prob_first")
            if pf is not None:
                lines.append(f"    予測 P(1着)={pf*100:.1f}% (オッズ確定後に再判定)")
        else:
            fp = a.get("fav_payout")
            if fp:
                lines.append(f"    本命オッズ: {fp}円")
        if racer:
            lines.append(f"    1号艇: {racer} (国1%={natl:.2f} / 局1%={local:.2f})")
        lines.append(f"    買い目推奨: {a.get('bet','?')}")
        lines.append(f"    詳細: {site}/race/{a.get('race_id','')}")
        lines.append("")
    lines.extend([
        "---",
        "本サービスは投資助言ではありません。最終判断はご自身で。",
        f"配信停止 (1クリック): {unsub}",
        f"サイト: {site}",
    ])
    text = "\n".join(lines)

    # html 版
    def rank_chip(rank):
        if rank == "plus_plus":
            return ('<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
                    'background:linear-gradient(135deg,#fbbf24,#f59e0b);color:#1f2937;'
                    'font-weight:bold;font-size:11px">🥇 L4++</span>')
        if rank == "plus":
            return ('<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
                    'background:linear-gradient(135deg,#d1d5db,#9ca3af);color:#1f2937;'
                    'font-weight:bold;font-size:11px">🥈 L4+</span>')
        return ('<span style="display:inline-block;padding:2px 8px;border-radius:10px;'
                'background:#374151;color:#fff;font-weight:bold;font-size:11px">⭐ L4</span>')

    rows = []
    for a in sorted(alerts, key=lambda x: x.get("race_closed_at", "")):
        rec = a.get('recovery', 0) or 0
        color = "#dc2626" if rec >= 200 else "#ea580c" if rec >= 150 else "#16a34a"
        natl = a.get("natl_1", 0) or 0
        local = a.get("local_1", 0) or 0
        racer = _esc(a.get("racer_name", "") or "-")
        rid_esc = _esc(a.get('race_id', ''), default="")
        # 朝モードのレースは行全体を薄い橙背景でマーク
        is_morn = a.get("mode") == "morning"
        row_bg = "background:#fef3c7;" if is_morn else ""
        morn_chip = ('<span style="display:inline-block;padding:1px 6px;margin-left:4px;'
                     'border-radius:8px;background:#f59e0b;color:#fff;font-size:10px;'
                     'font-weight:bold">🌅朝候補</span>') if is_morn else ""
        # 朝モードは prob_first / 確定モードは fav_payout を表示
        if is_morn:
            pf = a.get("prob_first")
            extra = f"P(1着)={pf*100:.1f}%" if pf is not None else ""
        else:
            fp = a.get("fav_payout")
            extra = f"本命 ¥{fp:,}" if fp else ""
        rows.append(f"""
        <tr style="border-bottom:1px solid #eee;{row_bg}">
          <td style="padding:8px;font-size:12px;color:#666">{_esc(a.get('race_closed_at'))}</td>
          <td style="padding:8px">{rank_chip(a.get('rank','base'))}{morn_chip}</td>
          <td style="padding:8px"><strong>{_esc(a.get('stadium_name'))} {_esc(a.get('race_number'))}R</strong></td>
          <td style="padding:8px"><span style="color:{color};font-weight:bold">{_esc(a.get('label',''))}<br><span style="font-size:11px;color:#666;font-weight:normal">回収 {rec:.1f}% / {_esc(extra)}</span></span></td>
          <td style="padding:8px;font-size:12px">
            <div>{racer}</div>
            <div style="color:#666;font-size:11px">国1%={natl:.2f} 局1%={local:.2f}</div>
          </td>
          <td style="padding:8px;font-size:13px">{_esc(a.get('bet'))}</td>
          <td style="padding:8px"><a href="{site}/race/{rid_esc}" style="color:#2563eb">詳細→</a></td>
        </tr>""")

    # ランク説明セクション
    rank_legend = f"""
    <div style="margin:16px 0;padding:12px;background:#f9fafb;border-radius:6px;font-size:12px">
      <div style="font-weight:bold;margin-bottom:6px">ランクについて</div>
      <div>{rank_chip('plus_plus')} 1号艇選手の国1%≥7.0 ∧ 局1%≥7.0 (検証回収率 <b>190.3%</b>)</div>
      <div style="margin-top:4px">{rank_chip('plus')} 1号艇選手の国1%≥7.0 (検証回収率 <b>188.2%</b>)</div>
      <div style="margin-top:4px">{rank_chip('base')} 基本 1号艇A1 (グレード別検証値)</div>
    </div>
    """

    if is_morning_only:
        html_heading = f'🌅 朝L4候補 {n} 件 ({rank_summary})'
        html_lead = ('<p><strong>オッズ確定前の予測ベース</strong>の候補リストです。'
                     'T-5min / T-15min オッズ取得後に再判定され、本命500-1000円帯に入らないものは取り消されます。</p>')
    elif has_morning:
        html_heading = f'🎯 L4 + 🌅朝候補 {n} 件 ({rank_summary})'
        html_lead = ('<p>確定 L4 と 朝の予測候補が混在しています。'
                     '朝候補は薄い橙背景でマークされています。</p>')
    else:
        html_heading = f'🎯 L4 シグナル {n} 件 ({rank_summary})'
        html_lead = '<p>検証回収率 <strong>150% 超</strong> の高確度レースが発見されました。</p>'

    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:880px;margin:auto;background:#f9fafb;padding:20px">
<div style="background:#fff;padding:24px;border-radius:8px">
<h2 style="margin-top:0">{html_heading}</h2>
{html_lead}
{rank_legend}
<table style="width:100%;border-collapse:collapse;margin-top:8px">
  <thead style="background:#f3f4f6">
    <tr>
      <th style="padding:8px;text-align:left;font-size:12px">締切</th>
      <th style="padding:8px;text-align:left;font-size:12px">ランク</th>
      <th style="padding:8px;text-align:left;font-size:12px">レース</th>
      <th style="padding:8px;text-align:left;font-size:12px">L4 タイプ</th>
      <th style="padding:8px;text-align:left;font-size:12px">1号艇 選手成績</th>
      <th style="padding:8px;text-align:left;font-size:12px">買い目</th>
      <th style="padding:8px"></th>
    </tr>
  </thead>
  <tbody>{"".join(rows)}</tbody>
</table>
<p style="margin-top:24px;font-size:12px;color:#999">
  本サービスは投資助言ではありません。最終判断はご自身で。<br>
  <a href="{unsub}" style="color:#666">配信停止 (1クリック)</a> |
  <a href="{site}" style="color:#666">サイトを開く</a>
</p>
</div>
</body></html>"""

    return _send(email, subject, text, html)
