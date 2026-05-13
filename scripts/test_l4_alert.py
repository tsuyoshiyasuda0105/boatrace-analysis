"""L4 アラートメール本体のレイアウト確認用テスト送信

実際の send_l4_alert() を呼んでサンプル L4 情報 3 件入りメールを送る。
HTML メールのレイアウト・色分け・配信停止リンクが正しく表示されるか確認用。

実行:
  .venv\\Scripts\\python.exe scripts\\test_l4_alert.py 送信先@example.com
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

from src.notifications.mailer import send_l4_alert


def main():
    if len(sys.argv) < 2:
        print("使い方: python scripts/test_l4_alert.py 送信先アドレス")
        sys.exit(1)

    to = sys.argv[1]

    # サンプル L4 (5件) - 3ランク (L4 / L4+ / L4++) を全部含む
    alerts = [
        {
            "race_id": "20260514-01-12",
            "stadium_name": "桐生",
            "race_number": 12,
            "race_closed_at": "2026-05-14 20:50",
            "label": "🥇L4 SG×A1 (国×局+)",
            "recovery": 258.2,
            "bet": "3連単 1-2-3",
            "alert_type": "L4_SG",
            "rank": "plus_plus", "rank_label": "L4++", "rank_emoji": "🥇",
            "natl_1": 7.85, "local_1": 8.21, "racer_name": "峰 竜太",
        },
        {
            "race_id": "20260514-04-09",
            "stadium_name": "多摩川",
            "race_number": 9,
            "race_closed_at": "2026-05-14 17:21",
            "label": "🥈L4 G1×A1 (国級+)",
            "recovery": 188.2,
            "bet": "3連単 1-2-3",
            "alert_type": "L4_G1",
            "rank": "plus", "rank_label": "L4+", "rank_emoji": "🥈",
            "natl_1": 7.32, "local_1": 6.45, "racer_name": "白井 英治",
        },
        {
            "race_id": "20260514-22-11",
            "stadium_name": "福岡",
            "race_number": 11,
            "race_closed_at": "2026-05-14 18:30",
            "label": "⭐L4 一般戦×A1",
            "recovery": 147.7,
            "bet": "3連単 1-2-3",
            "alert_type": "L4_general",
            "rank": "base", "rank_label": "L4", "rank_emoji": "⭐",
            "natl_1": 6.12, "local_1": 5.89, "racer_name": "佐藤 翼",
        },
        {
            "race_id": "20260514-15-08",
            "stadium_name": "丸亀",
            "race_number": 8,
            "race_closed_at": "2026-05-14 16:42",
            "label": "🥇L4 G2×A1 (国×局+)",
            "recovery": 190.3,
            "bet": "3連単 1-2-3",
            "alert_type": "L4_G2",
            "rank": "plus_plus", "rank_label": "L4++", "rank_emoji": "🥇",
            "natl_1": 7.10, "local_1": 7.45, "racer_name": "深谷 知博",
        },
        {
            "race_id": "20260514-05-11",
            "stadium_name": "多摩川",
            "race_number": 11,
            "race_closed_at": "2026-05-14 19:00",
            "label": "🥈L4 一般戦×A1 (国級+)",
            "recovery": 188.2,
            "bet": "3連単 1-2-3",
            "alert_type": "L4_general",
            "rank": "plus", "rank_label": "L4+", "rank_emoji": "🥈",
            "natl_1": 7.42, "local_1": 6.10, "racer_name": "毒島 誠",
        },
    ]

    unsub_token = "DUMMY_TOKEN_FOR_TESTING_PLEASE_IGNORE_THIS_LINK"

    print(f"→ {to} に L4 サンプル ({len(alerts)}件) を送信...")
    ok = send_l4_alert(to, alerts, unsub_token)
    print()
    if ok:
        print(f"✅ 送信成功 → {to} の受信箱で HTML メールを確認")
    else:
        print("❌ 送信失敗 (上のエラーログ確認)")
        sys.exit(2)


if __name__ == "__main__":
    main()
