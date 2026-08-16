# 勝ち筋サーチ Step 12 実装結果

実施日: 2026-08-16

## 廃止理由

買い目の払戻オッズは実際に的中した組番にしか存在せず、これを検索条件にすると外れレースが分母から脱落して回収率が異常に膨張するため、買い目オッズ帯 `odds` と人気帯 `t5_odds_favorite` の両方を検索条件から廃止した。回収率は条件に合う全レースを分母として計算する。将来、T-5min オッズを締切前の事前絞り込みとして安全に定義し直す余地は、データ資産を温存することで残した。

## 変更内容

- `src/search/roi_search.py`: 両キーを条件ホワイトリストとSQL生成から外し、指定時は「オッズによる絞り込みは廃止されました。回収率は条件に合う全レースを分母に計算します」で拒否する。`odds_snapshot` JOINは削除した。
- `src/search/strategies.py`: オッズを同日未確定とする pending 判定とJOINを削除した。保存済み手法の実行前検査を追加した。
- `src/kachisuji_web/app.py`: 廃止理由と保存済み手法向けの日本語案内をAPIレスポンスへ通すようにした。
- `src/kachisuji_web/templates/search.html`: 買い目オッズ帯・人気帯の入力、説明、バッジ、凡例、条件JSON組み立て処理を削除した。
- `src/kachisuji_web/static/kachisuji.css`: 撤去したオッズUI専用スタイルを削除した。
- `tests/test_roi_search.py`, `tests/test_strategies.py`, `tests/test_kachisuji_web.py`, `tests/e2e/test_kachisuji_e2e.py`: 廃止キー、保存済み手法、通常回帰、UI不在の契約へ更新した。

## 保存済み手法の扱い

過去に `odds` または `t5_odds_favorite` を保存した手法は削除・非表示にせず、`/api/strategies` の一覧取得では条件JSONを含めて従来どおり返す。一方、個別照合、一括照合、個別成績、成績一覧は HTTP 400 とし、「この手法はオッズ条件を含むため実行できません。条件を編集してください」と案内する。これは Step 8 の旧 `final` 手法と同じ、資産を残して危険な再実行だけを拒否する方針である。

## 温存したデータ資産

- `odds_snapshot` テーブルと同期処理
- `asof_race_features.t5_odds_favorite` 列と生成処理
- 既存の保存済み手法レコードと条件JSON

`src/features/asof_builder.py`、`src/features/odds_sync.py`、実物DB、DDLには変更を加えていない。

## テスト結果

- 合成フィクスチャ focused: 120 passed
- クロスチェック互換性を含む合成・契約テスト: 127 passed
- メインE2E: 56 passed、終了後のポート8090リスナー 0
- round3 E2E: 3 passed、終了後のポート8091リスナー 0
- 全non-E2E: 921 passed
- Pythonコンパイル、`git diff --check`: 成功

## 既知の制限

オッズ条件を含む保存済み手法を画面上で自動変換はしない。利用者が条件を編集してオッズキーを除く必要がある。T-5min を将来復活させる場合は、回収率の事後フィルターではなく締切前に確定する事前選定条件として、分母を壊さない契約を別途定義する必要がある。
