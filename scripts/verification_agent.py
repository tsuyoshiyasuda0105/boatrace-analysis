"""検証エージェント CLI エントリポイント。

役割:
  1. config/verification_sources.json から URL リストを読込
  2. 各 URL から本文テキストを取得
  3. extract.extract_methods で候補手法を抽出
  4. backtest.backtest_method で DB 検証
  5. report.write_report で markdown レポートを出力

ポリシー (再掲):
  - **発見と検証のみ**: 本番戦略コードへの自動反映は一切しない。
  - 出力は markdown のみ (reports/ ディレクトリ)。

使い方:
    python scripts/verification_agent.py
    python scripts/verification_agent.py --sources config/verification_sources.json
    python scripts/verification_agent.py --output reports
    python scripts/verification_agent.py --text "<本文を直接渡す>"
    python scripts/verification_agent.py --dry-run     # 抽出と検証のみ、ファイル出力なし
    python scripts/verification_agent.py --include-discard  # discard も markdown に含める
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

import config  # noqa: E402
from src.verification.extract import extract_methods, title_of  # noqa: E402
from src.verification.backtest import backtest_method  # noqa: E402
from src.verification.report import write_report  # noqa: E402

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


def fetch_text(url: str) -> str | None:
    """URL から本文テキストを取得 (BeautifulSoup でタグ除去)。失敗時 None。"""
    try:
        from src.collectors._http import fetch_html
    except ImportError:
        logger.warning("src.collectors._http が import 不可 → URL 取得スキップ")
        return None
    html = fetch_html(url)
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return html  # fallback: 生 HTML を返す
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def load_sources(path: Path) -> list[str]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        logger.error("sources JSON 読込失敗: %s", e)
        return []
    return [u for u in data.get("urls", []) if isinstance(u, str) and u]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources",
                        default="config/verification_sources.json",
                        help="URL リスト JSON")
    parser.add_argument("--text",
                        help="URL の代わりにテキスト直接指定 (テスト用)")
    parser.add_argument("--output", default="reports",
                        help="markdown 出力ディレクトリ")
    parser.add_argument("--dry-run", action="store_true",
                        help="ファイル出力せず標準出力のみ")
    parser.add_argument("--interval", type=float,
                        default=config.REQUEST_INTERVAL_SECONDS,
                        help="URL 取得間隔 (秒)")
    args = parser.parse_args()

    all_methods: list[dict] = []

    # 入力: テキスト直接 or URL リスト
    if args.text:
        print(f"=== 検証エージェント (text入力 {len(args.text)} 文字) ===")
        all_methods.extend(extract_methods(args.text, source_url="(text input)"))
    else:
        urls = load_sources(Path(args.sources))
        if not urls:
            print(f"※ 巡回対象 URL がありません: {args.sources}")
            print("  config/verification_sources.json の 'urls' に追加してください。")
            print("  または --text \"<本文>\" で直接テキストを渡せます。")
            return 1
        print(f"=== 検証エージェント (sources={len(urls)}) ===")
        for url in urls:
            print(f"  fetching {url}")
            text = fetch_text(url)
            if not text:
                print(f"    [SKIP] 取得失敗")
                continue
            methods = extract_methods(text, source_url=url)
            print(f"    {len(methods)} 件抽出")
            all_methods.extend(methods)
            time.sleep(args.interval)

    if not all_methods:
        print("\n候補手法が 1 件も抽出されませんでした。")
        return 0

    print(f"\n抽出された候補手法: {len(all_methods)} 件")
    print("DB で検証中...")
    for m in all_methods:
        try:
            m["backtest"] = backtest_method(m)
        except Exception as e:  # noqa: BLE001
            logger.exception("backtest 失敗: %s", e)
            m["backtest"] = {"error": str(e), "tier": "discard"}

    # 標準出力サマリ
    print("\n--- 検証結果サマリ ---")
    for m in all_methods:
        bt = m.get("backtest") or {}
        print(f"  [{bt.get('tier', '?'):<20}] "
              f"ROI={bt.get('roi', 0):6.1f}%  "
              f"n={bt.get('n_races', 0):>7}  "
              f"{title_of(m)[:50]}")

    if args.dry_run:
        print("\n--dry-run のためレポートファイルは作成しません")
        return 0

    fpath = write_report(all_methods, Path(args.output))
    print(f"\nレポート出力: {fpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
