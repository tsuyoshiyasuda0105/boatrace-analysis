# slim DB ディスク逼迫修理 作業ログ

- 作業日: 2026-08-20
- 実装コミット: `7964324` (`Fix kachisuji slim disk pressure`)
- 対象指示書: `reports/slim_disk_pressure_fix_spec_20260820.md`

## 方式選択と根拠

方式 (a) の「単一 SQLite トランザクション」を採用した。

- 573MB の slim DB 全量コピーを作らないため、1GB 永続ディスクで 573MB × 2 が必要になる構造を除去できる。
- `BEGIN IMMEDIATE` から `COMMIT` までに、`applied_deltas` テーブル作成、全未適用デルタの `INSERT OR IGNORE`、各デルタの `applied_deltas` 記帳を含めた。途中のスキーマ不一致・SQLite例外では全体を `ROLLBACK` するため、複数デルタの途中までが残らない。
- デルタDBは読み取り専用接続から行をストリーミングし、slim側へバインド変数付き `executemany` で適用する。全デルタを同時に `ATTACH` しないため、SQLite の attached database 数上限にも依存しない。
- web経路 `src/kachisuji/delta_transport.py` と旧CLI経路 `scripts/apply_kachisuji_deltas.py` を同じトランザクション方式へ揃えた。

方式 (b) は採用しなかった。空き容量に応じてバックアップ有無が変わる二重契約を残すより、常に同じ原子性契約にする方が故障時の状態を説明・検証しやすいためである。

## 変更点

- `src/kachisuji/delta_transport.py`
  - slim DB の `.bak` 全量コピーと復元処理を廃止。
  - 全pendingデルタを1トランザクションで適用。
  - スキーマ検証、`INSERT OR IGNORE`、`applied_deltas` 記帳、二重適用防止を維持。
  - 空き容量閾値を100MiB (`104857600` bytes) とし、pendingがある状態で未満なら適用開始前に `InsufficientDiskSpaceError`。
  - 成功時・pendingなし時のsummaryに `free_bytes` を追加。
- `scripts/apply_kachisuji_deltas.py`
  - 同じ全量コピーを廃止し、web経路と同じ単一トランザクション契約へ変更。
- `src/web/kachisuji_bp.py`
  - 空き容量不足を汎用500ではなくHTTP 507で返し、JSONに `free_bytes` と `required_bytes` を含める。
- `scripts/render_maintenance_scheduler.py`
  - slim DB配置先の空き容量測定を追加。
  - 100MiB以上を条件とする14番目のpreflight checkを追加。`critical=false` のためメンテナンス延長を単独では発生させず、warningとして可視化する。
- 回帰テスト
  - 良品デルタ適用後に後続デルタのスキーマ不一致が起きても、行・`applied_deltas`・テーブル作成がすべてロールバックされることを確認。
  - 旧バックアップ復元テストを新トランザクション方式に更新。
  - 空き容量不足時の507、容量値、適用前停止を確認。
  - 両applyソースに `shutil.copy2` が存在せず、明示トランザクションを持つ静的チェックを追加。
  - preflightの閾値未満判定と非critical性を確認。

`src/web/templates/base.html` のナビ・日付フォーム、`render.yaml`、ROI判定ロジックは変更していない。

## テスト結果

- focused: `90 passed`
- 指定非E2E全体: `1172 passed, 1 skipped`（基準 `1169 passed` を維持）
- Ruff（変更Python 8ファイル）: pass
- `py_compile`（変更実装4ファイル）: pass
- `git diff --check`: pass
- `shutil.copy2` apply経路静的検索: 0件
- `base.html` / `render.yaml` 差分: なし

全テストはローカルfixture・SQLite・mockで実施した。本番Supabase書込み、本番`/data`操作、ローカル本番スケジューラ、push、deployは実施していない。

## 症状Bと運用上の提案

本修理では指示どおりUIマークアップを変更していない。`/member/today-races` の本番再計測は、本番操作禁止のため未実施。全量コピー失敗とディスク逼迫を解消したデプロイ後に、既存の `system_status.slow_request` で残存遅延を確認する。

`render.yaml` のdisk設定は変更していない。573MBのDBが今後成長し、100MiB警告が継続する場合は、発注者判断で永続ディスク容量を増やすことを提案する。これは本修理の原子性には不要だが、SQLite journalと通常成長分の運用余裕を確保するためである。
