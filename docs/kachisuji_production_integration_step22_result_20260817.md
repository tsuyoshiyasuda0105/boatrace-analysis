# 勝ち筋サーチ Step 22 本番統合結果

実施日: 2026-08-17

## 結果

勝ち筋サーチを `src/web/app.py` の既存 Flask アプリへ Blueprint として登録した。
本番アプリの既存ルート・検索計算・手法計算には変更を加えず、検索DBが未配置でも
アプリ起動、`/healthz`、`/races` が動作し、`/kachisuji` だけが準備中表示になることを
回帰テストで確認した。push、デプロイ、本番データ配置は実施していない。

## 追加ファイル

| ファイル | 追加行 | 内容 |
|---|---:|---|
| `src/web/kachisuji_bp.py` | 356 | `/kachisuji` と `/kachisuji/api/*` の Blueprint、遅延DBパス解決、未配置フォールバック |
| `scripts/export_kachisuji_slim_db.py` | 139 | 2テーブル限定の読み取り専用スリムDB生成・VACUUM・検証CLI |
| `src/web/templates/kachisuji_search.html` | 1,013 | 本番 `base.html` 継承版の検索画面と準備中表示 |
| `src/web/static/kachisuji.css` | 254 | 検索画面CSS（本番ヘッダーへ汎用スタイルを漏らさないスコープ） |
| `tests/test_kachisuji_production_integration.py` | 244 | 認証、API、合成DB、未配置回帰、エクスポータ、Renderコメントのテスト |
| `docs/kachisuji_production_integration_step22_result_20260817.md` | 101 | 本結果レポート |

## 変更した既存ファイル

| ファイル | 変更行数 | 内容 |
|---|---:|---|
| `src/web/app.py` | +2 / -0 | Blueprint import 1行、登録1行のみ |
| `src/web/templates/base.html` | +3 / -0 | 既存の会員ナビ内に「勝ち筋サーチ」リンクを1箇所追加 |
| `render.yaml` | +7 / -0 | 無効状態のコメントだけで1GBディスク例と環境変数例を追記 |

共有運用ログ `docs/handoff.md` は作業記録として更新したが、既存の他タスク記録を含むため
Step 22 コミット対象から除外する。

## 認証の再利用

- HTML画面は既存 `login_required` をそのままデコレータとして使用する。
- ログイン済みでも既存 `is_paid_member()` が偽なら既存403ハンドラへ渡す。
- 全APIは既存 `member_only_api` をそのまま使用し、未認証を401にする。
- APIも既存 `is_paid_member()` を追加確認し、無料会員からの直接呼び出しを403にする。
- 新しい資格情報、セッションキー、ロール判定、ログインルートは作成していない。

## DBパスと未配置フォールバック

- 検索DBは `KACHISUJI_DB` を最優先する。未指定時は
  `data/kachisuji_slim.db`、次に既存 `data/kachisuji_search.db` を選ぶ。
- 手法DBは `KACHISUJI_STRATEGY_DB`、未指定時は
  `data/kachisuji_strategies.db` を使用する。
- Blueprint import/登録時には検索DBを開かない。各リクエストでパスを遅延解決する。
- 検索DBが無い場合、有料会員の `/kachisuji` はHTTP 200で「準備中」を表示する。
- DBを必要とするAPIはHTTP 503と `kachisuji_unavailable` を返す。
- 合成テストでは存在しないパスにDBが新規作成されず、同じアプリの `/healthz` と
  スナップショット経由の `/races` がHTTP 200を維持した。

## スリムDB生成確認

合成元DBに `asof_race_features`、`racers`、不要テーブルを作り、CLIを `--verify` 付きで実行した。
出力は `asof_race_features=1行`、`racers=1行`、`PRAGMA quick_check=ok`、表示サイズ0.0 MiB。
不要テーブルは出力されず、両テーブルの明示インデックスが複製された。生成前後で元DBの
SHA-256が同一であり、元DBは読み取り専用URIで接続される。実データで想定される約566MBの
生成は仕様どおりリンが実施するため、本作業では実行していない。

## render.yaml の提案

`boatrace-web` の既存設定直後に、すべてコメントのまま次を提示した。

- ディスク名: `kachisuji-data`
- マウント先: `/data`
- サイズ: 1GB
- `KACHISUJI_DB=/data/kachisuji_slim.db`
- `KACHISUJI_STRATEGY_DB=/data/kachisuji_strategies.db`

既存Webサービス、cron、プラン、コマンド、環境変数の有効設定は変更していない。

## デプロイ手順の下書き（未実施）

1. 課金を伴う1GB永続ディスクの有効化について承認を得る。
2. 信頼できる作業環境で実データ元DBを読み取り専用のまま
   `python scripts/export_kachisuji_slim_db.py --out data/kachisuji_slim.db --verify`
   により生成し、行数・`quick_check`・実サイズを確認する。
3. スリムDBをRenderの `/data/kachisuji_slim.db` へ安全に配置する。Gitには追加しない。
4. 永続ディスクを `/data` にマウントし、上記2環境変数を設定する。
5. 承認済みコミットをデプロイし、最初に `/healthz` と既存 `/races` を確認する。
6. 未認証、無料会員、有料会員で画面/APIの401・403・200を確認する。
7. 検索、手法保存、成績、当日照合、選手検索をスモーク確認し、ディスク使用量とエラーログを監視する。

## テスト結果

- 最終集中テスト: 14 passed。
- 全非E2E: 1,048 passed / 1 skipped。
- 既存メインE2E: 77 passed。
- 既存Round 3 E2E: 3 passed。
- 重複を除く既存全体: 1,128 passed / 1 skipped。
- Ruff（新規Pythonとテスト）、Pythonコンパイル、`git diff --check`: 合格。
- pytestの既知警告は既存 `.pytest_cache` ACLによるキャッシュ書込み警告のみ。

## 既知の制限

- 実データの566MBスリムDB生成、Render配置、課金ディスク有効化、push、デプロイは未実施。
- 手法DBはSQLite永続ディスク上の既存ローカル所有者モデルを再利用する。複数Webインスタンスや
  会員ごとの手法分離が必要になった場合は別ステップで設計が必要。
- `src/search/roi_search.py`、`src/search/strategies.py`、`src/kachisuji_web/` は変更していない。
