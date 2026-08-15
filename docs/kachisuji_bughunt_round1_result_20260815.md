# 勝ち筋サーチ Playwright バグハント Round 1 実行サマリ

## 結果

- 実行日: 2026-08-16 JST
- 環境: Windows / Python 3.14.3 / pytest 9.0.3 / Playwright Chromium headless
- コマンド: `.venv/Scripts/python.exe -m pytest tests/e2e --basetemp .pytest-tmp-kachisuji-round1-final2 -q`
- 実行シナリオ数: 49（S1〜S7すべてを含む）
- pytest: 46 passed / 3 xfailed / 0 failed（51.36秒）
- 検出バグ: 3件（Critical 0 / High 0 / Medium 3 / Low 0）

## 主要バグ

1. BUG-001: 重複した買い目を拒否するが、内部向け英語バリデーション文言を日本語UIへ露出する。
2. BUG-002: 同一艇比較を拒否するが、内部フィールドパスを含む英語文言を表示する。
3. BUG-003: `/api/strategies` が非objectの backtest を200で保存し、壊れた型を一時strategy DBへ永続化する。

詳細は `reports/kachisuji_bug_list_20260815.md` を参照。

## シナリオ証跡

- S1: トップ/主要セクション、単勝・2連単・3連単切替、実DB検索とKPI。
- S2: 重複艇番、単勝/2連単の不要着順非送信。
- S3: 6艇の開閉、級別複数選択とバッジ、選手名400、比較追加削除・単位・同一艇比較。
- S4: 負値・極端値・非数値・逆範囲、日付逆転/不正/未来、無条件全件検索と30秒上限。
- S5: 空名拒否、保存/一覧/削除、XSS、20件大量保存、confirmed/pending照合。
- S6: 未知キー、型違い、1MB JSON、壊れたstrategy payload、不正match日付、healthz。
- S7: 390px横オーバーフロー、検索二重クリック、console error収集。

## 隔離・安全確認

- サーバーはテスト session fixture が `scripts/run_kachisuji_web.py --port 8090` を subprocess 起動し、`finally` で terminate、必要時kill、waitを実施する。
- 最終実行後 `PORT_8090_LISTENERS=0`。
- `KACHISUJI_DB` は `data/kachisuji_search.db` のみ。`data/boatrace.db` は実行前後の更新tickが一致し、`BOATRACE_DB_UNCHANGED=True`。
- `KACHISUJI_STRATEGY_DB` は各pytest実行の一時ディレクトリ配下。実運用strategy DBは使用していない。
- 外部ネットワーク、スケジューラ、デプロイ、pushは実施していない。製品コードの変更もない。

## 注意

- pytestの既存 `.pytest_cache` に対する Windows のキャッシュ作成警告が1件出たが、テスト結果・一時DB隔離・fixture teardownには影響しなかった。
- コミットハッシュは自己参照になるため本文には埋め込まず、最終出力で報告する。
