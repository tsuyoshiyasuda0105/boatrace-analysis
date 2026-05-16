# CLAUDE.md — BOATRACE 予測プロジェクト

このファイルは Claude (AI アシスタント) がこのリポジトリで作業する際のコンテキスト。
人間向けドキュメントは [README.md](./README.md) を参照。

> **💡 エージェントチーム** が有効化されています (Claude Code v2.1.32+ 実験的機能)。
> 並列調査・コードレビュー・新機能開発の際は `.claude/AGENT_TEAMS.md` を参照し、
> `data-collector` / `ml-engineer` / `web-developer` / `scheduler-ops` / `db-optimizer`
> のサブエージェントを召喚してください。

---

## プロジェクト概要

ボートレース 1着・三連単 予測 + 期待値ベース Value Bet 検出システム。

**目標**: 控除率 25% の市場で +EV を掴む（現実的目標 ROI 0〜+5%）。

**現状**: PerWinner Cascade で**単勝固定 ROI -7.05%** が最良。完全に +EV には未到達。Layer 3 オッズが揃った後の Value Bet 検出が次のレバー。

---

## ディレクトリ構成

```
C:\boat_project\boatrace-analysis\
├── config.py                      # 全設定 (パス・URL・閾値・認証)
├── data/
│   ├── boatrace.db                # SQLite メインDB (~150MB, 224K races)
│   └── raw/                       # 生スクレイピング結果
│       ├── programs/              # Layer 1 番組表 LZH/TXT
│       ├── results/               # Layer 1 競走成績 LZH/TXT
│       └── beforeinfo/, odds3t/   # Layer 3 HTML
├── master/                        # 24会場マスタ等
├── src/
│   ├── db/connection.py           # SQLite ヘルパー (WAL モード必須)
│   ├── collectors/                # データ取得層
│   │   ├── openapi.py             # Layer 2 ✅
│   │   ├── beforeinfo.py          # Layer 3 直前情報 (parts) ✅
│   │   ├── odds.py                # Layer 3 オッズ ✅
│   │   ├── official_dl.py         # Layer 1 LZH downloader ✅
│   │   └── _http.py               # 共通 HTTP (rate limit 2.0s/req)
│   ├── parsers/                   # HTML/SJIS パーサー
│   │   ├── beforeinfo.py, odds.py # Layer 3 用
│   │   └── official_b.py, official_k.py # Layer 1 用 (cp932 固定幅)
│   ├── features/builder.py        # 特徴量生成 (52 列)
│   ├── models/
│   │   ├── train.py               # LGBMRanker 学習 (Stage 1)
│   │   ├── calibration.py         # Isotonic 較正 (1着/2着以内/3着以内)
│   │   ├── cascade.py             # Stage 2/3 unified カスケード
│   │   ├── cascade_per_winner.py  # PerWinner 6モデル (1着レーン別) ⭐
│   │   ├── pattern_features.py    # 経験的パターン特徴量 (使わなくて良い)
│   │   └── joint_calibration.py   # joint trifecta 確率の Isotonic 較正
│   ├── analysis/
│   │   └── decay_factor.py        # オッズ減衰率 (T-5min→final)
│   ├── evaluation/                # 評価系
│   │   ├── value_bet.py           # 単勝・三連単 EV 計算
│   │   ├── backtest.py            # Walk-Forward / single-split
│   │   ├── evaluate_with_payouts.py    # race_payouts のみで簡易ROI
│   │   ├── evaluate_cascade.py    # Cascade 評価
│   │   ├── value_bet_trifecta.py  # フル EV (Layer 3 オッズ前提)
│   │   ├── subgroup_analysis.py   # 会場別/グレード別 ROI
│   │   ├── bootstrap_ci.py        # ROI 95%CI
│   │   ├── bootstrap_edge_ci.py   # edge 帯別 CI
│   │   └── market_vs_model.py     # 市場 implied vs モデル予測 比較
│   └── web/
│       ├── app.py                 # Flask アプリ
│       ├── predictor.py           # 予測ラッパー (キャッシュ + What-if)
│       ├── auth.py                # 会員認証 (パスワード+セッション)
│       ├── templates/             # Jinja2 (base/index/race.html)
│       └── static/style.css       # ダーク UI
├── scripts/                       # CLI スクリプト
│   ├── daily_collect.py           # Layer 2 日次取得 (タスクスケジューラ用)
│   ├── daily_collect.ps1          # PS ラッパー
│   ├── scrape_layer3.py           # Layer 3 取得
│   ├── backfill_official.py       # Layer 1 LZH バックフィル
│   ├── train_cascade.py / train_cascade_per_winner.py
│   ├── retrain_full_pipeline.py   # 全モデル一括再学習
│   ├── optuna_tune_ranker.py      # Stage 1 ハイパラ最適化
│   ├── odds_scheduler.py          # T-5/T-1分前オッズスナップショット
│   ├── eval_cascade_per_winner.py / eval_top1_with_odds.py
│   ├── analyze_confidence_roi.py
│   └── run_web.py                 # Web UI 起動
├── models_artifacts/              # 学習済モデル
│   ├── ranker_<v>.pkl             # Stage 1 (LGBMRanker + 較正器)
│   ├── cascade_cascade-<v>.pkl    # Stage 2/3 unified
│   ├── cascade_pw_pw-<v>.pkl      # PerWinner 6モデル
│   ├── joint_calib_*.pkl          # 三連単確率較正
│   └── calibrators_<v>.pkl        # 1着確率較正 (Isotonic)
└── logs/                          # 日次ログ (UTF-8)
```

---

## モデル世代と現状

| バージョン | 学習データ | 結果 | 備考 |
|---|---|---|---|
| v0.1 | 1年 (基本特徴量 41) | NDCG@1 0.690, Top1 hit 57.4% | 初期ベースライン |
| **v0.2** ⭐ | 1年 + 拡張特徴量 52 + cascade + pw | **PerWinner Top-1 ROI -7.05%** | **現状ベスト**, Web UI もこれ |
| v0.3 | v0.2 + Optuna ハイパラ | NDCG +0.36pt **だが ROI -10.6%** | **逆効果**、不採用 |
| v0.4 | 4年データ (2022-05〜) | **ROI -28%** | データドリフト、不採用 |
| v0.5 | 2年データ (2024-05〜) | (学習中) | 直近重視 |

**重要な学び**:
- **大量データ ≠ 良い model**。古いモーター番号・引退選手等で 4年データは逆効果
- Optuna で NDCG@1 を最適化しても下流の ROI は改善せず
- **PerWinner cascade (1着レーン別 6モデル)** はユーザー仮説に基づく工夫で唯一 +ROI に貢献

**未解決**: +EV 領域到達。次のレバー候補：
1. Layer 3 オッズ (T-5min/T-1min/final 3スナップショット) で本格 Value Bet
2. 時間重み付き学習 (recency-weighted)
3. アンサンブル (v0.2 + v0.5)

---

## データソース 3層

| Layer | URL | 取得期間 | 状態 |
|---|---|---|---|
| **Layer 1** 公式DL | `mbrace.or.jp/od2/{B,K}/...lzh` | 2022-05〜 4年分 | ✅ バックフィル完了 |
| **Layer 2** Open API | `boatraceopenapi.github.io/...json` | 2025-05〜 | ✅ 365日分 + 日次更新 |
| **Layer 3** スクレイピング | `boatrace.jp/owpc/pc/race/{beforeinfo,odds3t}` | **当日のみ** | 🔄 odds 30日 backfill 中 |

**重要な制約**:
- **Layer 1 LZH**: cp932 (Shift_JIS) 固定幅、7-Zip (`C:\Program Files\7-Zip\7z.exe`) で解凍
- **Layer 3 部品交換**: **過去日は表示されない**。当日のみ取得可能、毎日収集が必須
- **Layer 3 オッズ**: 過去日も取れる (確定オッズ)。-5min/-1min は当日リアルタイム

---

## DB スキーマ要点

### `odds_trifecta` (重要)
2026年5月マイグレーションで `snapshot_label` カラム追加：
- `T-1d` (24h前、大きいレースのみ)
- `T-5min`
- `T-1min`
- `final` (確定後)
- `intermediate` (途中、未使用)

PK = (race_id, combination, recorded_at)。Multiple snapshots per race × combination。

### `decay_factor` (新規)
オッズ減衰率テーブル：
- `bucket` (1-5x, 5-10x, ..., 1000+x), `mean_decay`, `n`, `updated_at`
- `decay_factor.compute_decay_table` で集計

---

## 重要な技術的ノウハウ

### 1. SQLite 並行アクセス
**必ず `src.db.connection.connect()` を使う**。直接 `sqlite3.connect()` は禁止。
- WAL mode + busy_timeout=30000ms
- 複数プロセス書き込み (scraper + Web UI + scheduler) で必須

### 2. Python 3.14 環境制約
- `lhafile` (LZH 解凍) はビルド失敗 → **7-Zip subprocess 経由**で解凍
- LightGBM 警告が stdout/stderr 大量に出る → `cascade._silence_native_stderr` で抑制 (OS-level fd dup)

### 3. Windows + PowerShell の落とし穴
- 日本語出力: `$env:PYTHONIOENCODING = "utf-8"` 必須
- ログ: PowerShell `*>>` redirect は **UTF-16 LE BOM** で書く → Python 側 `--log-file` で UTF-8 直接書き込み
- バックグラウンド: `Start-Process` は親シェル終了で死ぬことあり → 長時間ジョブはチャンク化 (`run_in_background=true` の bash で 8分ずつ)

### 4. Cascade prediction の遅さ
- 1レース予測 ~1.5秒 (PerWinner は 6 winners × 5/4 candidates の予測必要)
- 全 144 レース処理は 4分超 → API レスポンスとしては不適
- **キャッシュ必須** (predictor 内 `_tri_cache` で日付別キャッシュ済)

### 5. Bootstrap CI が必須
- サブグループ ROI 点推定だけでは騙される
- 30日 odds 程度では n=258 でも CI [-40%, +70%] と巨大
- `src/evaluation/bootstrap_ci.py` で 1000-2000 iter 必須

### 6. データドリフト
- 4年データで学習すると古いモーター番号が悪さする
- 2-3年が現実的な学習期間 (v0.5)

---

## Web UI

- **公開機能**: 全レース閲覧、6艇予測、三連単 Top-10 (PerWinner + Unified)、What-if シミュレーター
- **会員限定**: EV+ レースマーク、Value Bet API、オンタイム判定
- **ログイン**: `/login`, パスワード `config.WEB_MEMBER_PASSWORD` (env `BOATRACE_MEMBER_PASSWORD`)
- **セッション**: 12時間 TTL, secret は `config.WEB_SESSION_SECRET` (env `BOATRACE_WEB_SECRET`)

### 起動
```powershell
python scripts/run_web.py --port 5050
# → http://127.0.0.1:5050/
```

### What-if シミュレーター
- レース全体: 風速 (0-12m スライダー) / 波高 / 気温 / 水温
- 艇別: 展示タイム / 展示ST / チルト (-0.5〜+3.0 セレクト)
- **DB の race_previews から現状値を初期表示**
- POST `/api/race/<id>/whatif` でリアルタイム再予測

---

## よく使うコマンド

### 学習・評価
```powershell
# 全モデル再学習 (ranker + cascade + pw + joint calib)
python scripts/retrain_full_pipeline.py --date-from 2024-05-08 --date-to 2026-05-09 --split-ratio 0.85 --version v0.5

# Cascade per-winner 評価
python scripts/eval_cascade_per_winner.py --base v0.5 --unified cascade-v0.5 --per-winner pw-v0.5 --date-from 2024-05-08 --date-to 2026-05-09 --split-ratio 0.85 --max-val-races 1500

# サブグループ分析 (会場別ROI等)
python -m src.evaluation.subgroup_analysis --version v0.2 --date-from 2025-05-08 --date-to 2026-05-08

# Bootstrap CI
python -m src.evaluation.bootstrap_ci --version v0.2 --date-from 2025-05-08 --date-to 2026-05-08 --n-iter 2000

# 市場 implied prob vs モデル
python -m src.evaluation.market_vs_model --version v0.2 --date-from 2025-05-08 --date-to 2026-05-08 --snapshot final
```

### データ取得
```powershell
# Layer 2 Open API 日次
python scripts/daily_collect.py --verbose

# Layer 1 公式DL バックフィル
python scripts/backfill_official.py --start 2022-05-08 --end 2025-05-07 --skip-existing

# Layer 3 オッズ (30日)
python scripts/scrape_layer3.py --backfill 30 --targets odds --no-save-html

# Layer 3 リアルタイム scheduler (1パスのみ、cron 用)
python scripts/odds_scheduler.py --verbose

# What-if 1日テスト
python scripts/test_layer3_parser.py --date 20260506 --jcd 1 --rno 1
```

### DB 確認
```powershell
python scripts/_check_db_dates.py    # 年別カバー範囲
python scripts/_check_odds.py        # オッズ収集状況
```

---

## 自動化（タスクスケジューラ）

### 登録済み
- `BoatraceDailyCollect`: 毎日 23:30 → Layer 2 取得 (`daily_collect.ps1`)

### 未登録 (推奨)
- `BoatraceOddsScheduler`: 毎分 → 締切 5/1 分前 オッズスナップショット (`odds_scheduler.py`)
- `BoatraceLayer3Parts`: 毎日 22:00 → 当日 parts 取得

```powershell
# 登録例
schtasks /Create /TN "BoatraceOddsScheduler" /SC MINUTE /MO 1 `
  /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -NonInteractive -File `"C:\boat_project\boatrace-analysis\scripts\odds_scheduler_pass.ps1`"" `
  /RL LIMITED /F
```

---

## 検証済み・確信できる事実

1. **戸田・芦屋・三国・蒲郡 は ROI 確実マイナス** (CI 完全に負側)
2. **edge 50%+ (モデル過信ゾーン) は ROI -50% 以下** (CI [-65%, -38%])
3. **PerWinner Cascade は Plackett-Luce より約 +9pt ROI** (v0.2 比較)
4. **4年学習は1年学習より悪化** (データドリフト)
5. **What-if で 3号艇チルト+3 にすると 3号艇が 2着争いから後退** (現実感覚と整合)

## 検証済み・**信用できなかった**結果

1. ❌ **edge 10-25% で +3.02% ROI**: Bootstrap CI で [-40%, +70%]、P(>0)=49%
2. ❌ **桐生×拮抗で +25% ROI** (n=32): CI [-32%, +89%], 信頼区間巨大
3. ❌ **パターン特徴量追加 (cascade-v0.2)**: 逆効果
4. ❌ **Optuna ハイパラ最適化 (v0.3)**: NDCG 微増だが ROI 悪化

---

## 法務・運用注意

### データ規約
- Open API: MIT License、コードは自由 (データは公式由来)
- 公式サイト: 私的使用は OK、**商用 (有料サブスク等) は要許諾** (BOAT RACE 振興会)

### 商用化前の必須準備
- [ ] BOAT RACE 振興会への問い合わせ
- [ ] 利用規約 (投資推奨でない、当たり保証なし)
- [ ] 特商法表示・プライバシーポリシー
- [ ] 18歳未満アクセス制御
- [ ] 賭博助言の境界線確認 (風適法・賭博法)

### スクレイピング規約
- `REQUEST_INTERVAL_SECONDS = 2.0` 秒厳守
- **並列禁止** (Celery で複数 worker 立てる場合は queue 1つに集約)
- User-Agent に連絡先を入れる (config.USER_AGENT)

---

## クラウド移行構想 (将来)

| 役割 | 候補 | 月額 |
|---|---|---|
| ソース管理 + CI/CD | GitHub | $0 |
| API + ワーカー | Render (Starter) | $14-21 |
| DB + Auth | Supabase (Free → Pro) | $0 → $25 |
| 決済 | Stripe | 売上の 3.6% |
| CDN/DNS | Cloudflare | $0 |
| 合計 (運用1年目) | | $22-50 |

詳細移行計画は議論済み（GitHub 化 → Postgres 移行 → FastAPI 化 → Celery → Stripe → RLS → 監視）。

---

## 課題・既知バグ

1. **Joint trifecta 確率の calibration**: 長い目を 10x 以上過大評価。30日 odds データでは fit 不十分
2. **Bootstrap CI の n 不足**: サブグループ n=30-50 では CI が広すぎ、結論出ない
3. **scheduler の race_closed_at パース**: HH:MM:SS のみのケースを race_date と合成する必要あり
4. **screenshot tool**: 一部ページ (アニメーション含む) で timeout 発生
5. **What-if レスポンス時間**: ~25秒。キャッシュなし全予測実行のため

---

## 次の優先打ち手

### 高優先度
1. **Odds 60-90日に backfill 拡張** → サブグループ CI 縮小
2. **scheduler を schtasks に登録** → T-5min/T-1min 自動取得開始
3. **decay_factor table を週次更新** → Value Bet EV 補正に組み込む
4. **避けるべき会場切り戦略を UI で表示** (戸田/芦屋/三国/蒲郡)

### 中優先度
5. **v0.5 評価 → v0.2 と比較** (現在学習中)
6. **時間重み学習** (recency-weighted) で v0.6 試行
7. **Walk-Forward Backtest** (1ヶ月窓 × 24窓)
8. **アンサンブル** (v0.2 70% + v0.5 30%)

### 低優先度（クラウド化フェーズ）
9. GitHub 化 + private repo
10. Postgres 移行
11. FastAPI 化
12. Stripe 統合

---

## 哲学的要点

**競艇市場で +EV を取るのは難しい**。控除率 25%、市場流動性高い、参加者多い。世界的に見ても安定 +EV システムは稀。

ただし以下のレバーで現実的に近づける可能性はある:
1. **市場が単純化しすぎているニッチ**: 「**1号艇 prob 30-50% の拮抗レース**」は市場が雑に拮抗扱いしている可能性
2. **直前情報の織り込み遅れ**: 締切 5分前のオッズドリフト
3. **会場特化モデル**: 24会場で同じモデルを使うのは粗い

最も大きいレバーは**「実 odds データを使った Value Bet 検出」**だが、Layer 3 オッズが大量に必要。これが現プロジェクトの最大の bottleneck。
