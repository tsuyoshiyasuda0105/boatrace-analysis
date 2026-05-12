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

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

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
    }


def _smtp_configured() -> bool:
    c = _get_config()
    return bool(c["host"] and c["user"] and c["password"])


def _send(to: str, subject: str, body_text: str, body_html: str = None):
    """SMTP 送信本体。未設定時はコンソール出力。"""
    cfg = _get_config()
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
        logger.warning("SMTP 未設定: メール送信スキップ (内容をログ出力)")
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
      {race_id, stadium_name, race_number, race_closed_at, label, recovery, bet, alert_type}
    """
    if not alerts:
        return False
    site = _get_config()["site_url"]
    unsub = f"{site}/alerts/unsubscribe?token={unsubscribe_token}"

    n = len(alerts)
    subject = f"[BOATRACE] 🎯 L4 シグナル発火 ({n}件) - 高確度レース発見"

    # text 版
    lines = [
        f"BOATRACE Alert: L4 シグナル {n} 件発火",
        "",
        "検証回収率 150% 超の高確度レースが発見されました。",
        "対象レース一覧 (締切時刻順):",
        "",
    ]
    for a in sorted(alerts, key=lambda x: x.get("race_closed_at", "")):
        lines.append(f"  ▶ {a.get('race_closed_at','?')} | "
                     f"{a.get('stadium_name','?')} {a.get('race_number','?')}R | "
                     f"{a.get('label','')} ({a.get('recovery',0):.1f}%)")
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
    rows = []
    for a in sorted(alerts, key=lambda x: x.get("race_closed_at", "")):
        rec = a.get('recovery', 0)
        color = "#dc2626" if rec >= 200 else "#ea580c" if rec >= 150 else "#16a34a"
        rows.append(f"""
        <tr style="border-bottom:1px solid #eee">
          <td style="padding:10px;font-size:12px;color:#666">{a.get('race_closed_at','?')}</td>
          <td style="padding:10px"><strong>{a.get('stadium_name','?')} {a.get('race_number','?')}R</strong></td>
          <td style="padding:10px"><span style="color:{color};font-weight:bold">{a.get('label','')} {rec:.1f}%</span></td>
          <td style="padding:10px;font-size:13px">{a.get('bet','?')}</td>
          <td style="padding:10px"><a href="{site}/race/{a.get('race_id','')}" style="color:#2563eb">詳細→</a></td>
        </tr>""")
    html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;max-width:800px;margin:auto;background:#f9fafb;padding:20px">
<div style="background:#fff;padding:24px;border-radius:8px">
<h2 style="margin-top:0">🎯 L4 シグナル {n} 件発火</h2>
<p>検証回収率 <strong>150% 超</strong> の高確度レースが発見されました。</p>
<table style="width:100%;border-collapse:collapse;margin-top:20px">
  <thead style="background:#f3f4f6">
    <tr>
      <th style="padding:10px;text-align:left;font-size:12px">締切</th>
      <th style="padding:10px;text-align:left;font-size:12px">レース</th>
      <th style="padding:10px;text-align:left;font-size:12px">L4 タイプ</th>
      <th style="padding:10px;text-align:left;font-size:12px">買い目</th>
      <th style="padding:10px"></th>
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
