# コードベース構造監査レポート (2026-08-13)

対象: boatrace-analysis (Web = Render / DB = Supabase / cron = Render + 一部ローカルPC)
目的: 「バグが多くて販売まで進めない」の根本原因特定と、直す順番の提示。
調査方法: Webアプリ本体 / cron・バッチ / データ層 の3方面を並行監査。

---

## 総論

**書き直しは不要。** ロジック自体はよく研究されており、周辺モジュール
(`roi_history.py`, `l4_strategy.py`, `membership.py`, `src/collectors/`) は健全。
バグが止まらない原因は個々のミスではなく、以下の **5つの構造問題** に集約される。

1. 作業コピーが9つに分裂し、本番と手元のコードが別物
2. app.py (21,429行) の巨大クロージャに全ロジックが閉じ込められ、同じルールが最大6箇所に複製
3. キャッシュが7層・バージョン定数62個・無効化はMarkdownのハッシュ頼み
4. 失敗が徹底的に沈黙する設計 (エラーが「シグナルなし」に化ける)
5. SQLite/Postgres 二重DBの変換層が特定条件で「静かに間違う」

直近400コミットの54.5%が fix/repair/guard 系。うち大半がキャッシュ整合の修理。

---

## P0: 今すぐ (これをやらないと他の修正が無意味/危険)

### P0-1. 作業コピーの一本化
- git worktree が9つ。ローカル main は origin/main より **55コミット遅れ**。
- 本番 (Render, autoDeploy) は origin/main を使うが、夜間の中核スケジューラ2本
  (`render_maintenance_scheduler.py`, `render_program_bootstrap_scheduler.py`) は
  ローカルに存在すらしない。→ ローカルで直しても本番は直らない。
- `codex/pushsync-top-badges` に未マージ35コミット。
- CLAUDE.md の 23:30/06:30 フロー記述は現実と乖離 (Windowsタスクはほぼ全部 Disabled、
  生きているのは `BoatracePcNightlyPrepare` 01:00 と `BoatraceLocalSupabaseSync` 23:45 のみ、
  しかも別チェックアウトから実行)。
- **対処**: origin/main を唯一の真実とし、必要コミットを救出後に古い worktree を削除。
  CLAUDE.md の運用記述を現実に合わせて書き直す。

### P0-2. 沈黙バグの可視化 (販売品として最重要)
- `_safe_signal_eval` (app.py:13959, 77箇所) が戦略評価中の例外を握りつぶし
  「条件不成立」と区別不能にする。**戦略が壊れても誰も気づけない = 商品の欠陥が無音で続く**。
- app.py に完全沈黙の `except: pass` が57箇所。
- `render_maintenance_scheduler.main()` / `render_program_bootstrap_scheduler.main()` は
  **常に return 0** → Render のcron失敗通知が永久に無効。
- メール通知 (`install_error_notifier`) は Flask にのみ配線。**cronは1本も通知しない**。
- 管理ダッシュボードに最重要2ジョブ (maintenance/bootstrap) が表示されず、
  スケジュール表記も render.yaml と不一致。
- **対処**: (a) `_safe_signal_eval` に戦略別失敗カウンタ→admin画面表示、
  (b) cron終了コードを正直に返す or 失敗時に system_status+メール、
  (c) ダッシュボードの表記修正。ロジック変更ゼロで可視化だけ先行。

### P0-3. BANリスクの止血 (データ供給が生命線)
- **決まり手の永久再スクレイプループ**: `result_html.py:200` が `race_kimarite: None` を
  ハードコード (パーサーが決まり手を抽出しない) のに、`result_scraper.py:249-268` は
  「決まり手が無いレース」を24時間窓で毎パス再スクレイプ → 全レースを5分毎に永久再取得。
  完全に無駄なリクエスト増幅。
- レート制限がプロセス内グローバル変数。同一 cadence (`*/5`) の3つのRender cronが
  並走し、実効 ~0.67秒/リクエスト = 規約2.0秒の3倍違反。CLAUDE.md「並列禁止」に抵触。
- `result_scraper.py:213-215` は L4絞り込み失敗時に **fail-open で全レース取得**に切替。
- `odds_scheduler_render.py` は多重実行ガードなし。
- **対処**: kimarite抽出を実装 or 再スクレイプクエリ削除 / 共有レートリミッタ or
  cron時間帯の分離 / fail-open を fail-closed に / odds cron にロック追加。

---

## P1: 構造修正 (バグの発生源を断つ)

### P1-1. app.py の戦略ロジック外出し
- `create_app` が **15,910行のクロージャ** (app.py:5520-21429)。
  41個の `_evaluate_*` (計4,038行) と `ROI_STRATEGIES` (53定義) が深さ2のネストに埋没。
  インポート不能 → 他スクリプトが同じルールを再実装 → 6重複製の根本原因。
- 重複の実例:
  - B除外8会場: 6箇所で独立定義 (app.py内にすら2変数 `EXCLUDE_B` と `EXCLUDE_B_VENUES`)
  - 払戻500-1000帯: 4通りの書き方 (うち1つは `<=1000` のoff-by-one)
  - グレード別期待値テーブル: 5箇所
  - SSOTとして作った `l4_strategy.py` から import されているのは関数1個だけ
- **対処**: `_evaluate_*` と `ROI_STRATEGIES` を `src/strategies/` へ機械的に脱出させ、
  純関数化。全消費者 (alerts/sync/result_scraper/prewarm) をそこへ向ける。
  「全コピーサイトがSSOTをimportしている」ことを assert するテストを追加。

### P1-2. キャッシュ署名の付け替えと無効化の完全化
- `strategy_definition_signature()` = **adopted_strategies.md (文書) のSHA1** が
  全キャッシュキーに混入。コードを直しても文書が同じなら古いキャッシュが配信され続け、
  文書のtypo修正で全キャッシュ吹き飛び、ファイル欠落時は "nosig" で世代が衝突 (全て無音)。
- `invalidate_cache()` は12個の lru_cache のうち6個しか消さない。
  メモリキャッシュはプロセス局所で、複数workerでは admin のクリアが1workerにしか効かない。
- **対処**: 署名を戦略モジュールのソースハッシュ (or 戦略オブジェクトのversion属性) に変更。
  "nosig" フォールバックは起動失敗に。invalidate はレジストリ走査式に。

### P1-3. 二重DB変換層の防御
- `_rewrite_sqlite_specific` の実証済み欠陥:
  (A) 末尾 `--` コメントで ON CONFLICT 句がコメント化。
  (B) PKマップ (24表) に無い表は **黙って `DO NOTHING`** = 上書きのつもりが更新拒否。
      該当12表 (l4_daily_summary, course1_stats_cache, race_tides, paper_trades ほか)。
  (C) `racer_accident_period_stats` は live SQLite の実PKとマップが不一致。
- `openapi.upsert_results` が INSERT OR REPLACE のまま (CLAUDE.md禁止パターン)。
  Layer 1 の start_timing 等を Open API の NULL で clobber し得る。
  `sync_to_supabase.py` も同パターンを再導入済み。
- `ingest_fan_handbook.py --local` は raw sqlite3.connect (WAL規約違反) + エラーでも exit 0
  → racers.gender の silent fail (CLAUDE.md 記載の既知事故) が再発可能なまま。
- **対処**: 変換層のユニットテスト新設 (欠陥A/B/C各ケース + 「全 INSERT OR REPLACE 対象表が
  PKマップにある」assert)。未登録表は例外に。upsert_results を COALESCE 化。
  raw connect 2箇所を db_connect に置換、errors>0 で非0終了。

### P1-4. cron の統一
- ロック機構が3種 (task_runsリース / advisory lock / status=running) 併存し相互不認識。
  task_runs への書き手が5実装で競合ルールが不一致。
- 回復パスが14種類以上のパッチ積層 (SCHEDULER_VERSION でカウンタをリセットする脱出口が象徴)。
- 本番で到達不能なコード約600行 (run_nightly / run_morning / run_hourly 系) が
  保守・テストされ続けている。
- スキップ時に「success」を記録する偽装成功 (`refresh_race_detail_after_exhibition.py:601`)。
- **対処**: ロックを advisory lock 1方式に統一 / task_runs 書き込みを1関数に集約 /
  到達不能コードを削除 / 回復は「何度実行しても安全 (冪等)」設計に寄せる。

---

## P2: 掃除 (規模を減らしてバグの住処をなくす)

- ローカルのテスト24件失敗 (359成功) を修理 or 削除 — 赤いテストは全員を守らない。
- 死んだテンプレ4枚 (1,806行 = 全体の30%)、死んだテーブル4つ (参照ゼロ、~76k行)、
  `check_post_run_integrity.py` の同名関数二重定義 (先勝ち側が死にコード)。
- パーサー6本にフィクスチャHTMLテスト追加 (data/raw に素材あり)。
  パーサー群は現在 warning ゼロ = HTML構造変更で無音全滅する。
- `page_html_cache` / `motor_inspection_raw_pages` の purge 追加。
- render_cache_predictions の `date.today()` がUTC (現状は明示 --date で回避中)。

---

## 販売に向けた含意

- 商品価値 = 「正しい買い目情報が毎日確実に届くこと」。現状は
  (a) 戦略が壊れても無音 (P0-2)、(b) データ供給がBANリスクに晒され (P0-3)、
  (c) 数字が3経路 (Python評価/SQL集計/キャッシュ上書き) で食い違い得る。
  課金開始前に P0 全部と P1-1/P1-2 は必須と判断。
- データ規約面: 商用化には BOAT RACE 振興会への許諾確認が必要 (CLAUDE.md 記載)。
  レート制限3倍違反 (P0-3) は許諾交渉の観点でも先に解消すべき。

---

## 修正順序のまとめ

| 順 | 項目 | 効果 | 規模感 |
|---|---|---|---|
| 1 | P0-1 作業コピー一本化 | 以降の全修正が本番に届く | 小 (半日) |
| 2 | P0-3 BAN止血 (再スクレイプループ/fail-open/ロック) | データ供給の保全 | 小-中 |
| 3 | P0-2 失敗の可視化 (カウンタ/通知/return code) | バグ発見が数週間→即日 | 小-中 |
| 4 | P1-1 戦略ロジック外出し | 6重複製の根絶、テスト可能化 | 中-大 |
| 5 | P1-2 キャッシュ署名付け替え | 最多バグ源の根治 | 中 |
| 6 | P1-3 DB変換層テスト+upsert修正 | 本番だけ壊れる系の根絶 | 小-中 |
| 7 | P1-4 cron統一 | 火消しループからの脱出 | 中-大 |
| 8 | P2 掃除 | 保守コスト削減 | 中 |
