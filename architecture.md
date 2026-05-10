# architecture.md — システムアーキテクチャ

このドキュメントはシステム全体の**技術アーキテクチャ**を記述する。
- ユーザー向け使い方は [README.md](./README.md)
- 開発者・AI向けコンテキストは [CLAUDE.md](./CLAUDE.md)
- このファイルは「なぜこの設計か」を説明する

---

## 1. 全体アーキテクチャ概観

```
┌──────────────────────────────────────────────────────────────────┐
│ External Sources (取得側)                                           │
├──────────────────────────────────────────────────────────────────┤
│  ① Layer 1: 公式 LZH (mbrace.or.jp/od2/B,K/...lzh)                  │
│     ├ B file: 番組表 (cp932 SJIS 固定幅, 7-Zip 解凍)                  │
│     └ K file: 競走成績 (同左)                                         │
│  ② Layer 2: Open API (boatraceopenapi.github.io/...json)            │
│     ├ Programs (出走表)                                              │
│     ├ Previews (直前情報)                                            │
│     └ Results (結果)                                                 │
│  ③ Layer 3: 公式サイト HTML (boatrace.jp/owpc/pc/race/...)          │
│     ├ beforeinfo (部品交換・展示タイム)                               │
│     └ odds3t (三連単オッズ — 5min/1min/final スナップショット)         │
└─────────────────┬─────────────────────────────────────────────────┘
                  │ HTTP (rate-limit 2.0s/req)
                  ↓
┌──────────────────────────────────────────────────────────────────┐
│ Collectors / Parsers                                                │
├──────────────────────────────────────────────────────────────────┤
│  src/collectors/                                                    │
│  ├ openapi.py        — Layer 2 collector                            │
│  ├ beforeinfo.py     — Layer 3 parts + 展示補完                      │
│  ├ odds.py           — Layer 3 odds スナップショット                  │
│  ├ official_dl.py    — Layer 1 LZH 取得 + 7-Zip 解凍                 │
│  └ _http.py          — 共通 rate-limited HTTP (グローバル interval)   │
│                                                                     │
│  src/parsers/                                                       │
│  ├ official_b.py / official_k.py — cp932 固定幅 (regex ベース)        │
│  ├ beforeinfo.py / odds.py       — BeautifulSoup (lxml + XML strip) │
└─────────────────┬─────────────────────────────────────────────────┘
                  │ INSERT / UPSERT
                  ↓
┌──────────────────────────────────────────────────────────────────┐
│ Storage Layer (SQLite + WAL)                                        │
├──────────────────────────────────────────────────────────────────┤
│  data/boatrace.db                                                   │
│  ├ races, race_entries, race_previews, race_results, race_payouts   │
│  ├ stadiums, racers, racer_period_stats                             │
│  ├ race_parts            (Layer 3 部品交換)                          │
│  ├ odds_trifecta         (snapshot_label: T-1d/T-5min/T-1min/final) │
│  ├ predictions, value_bets                                          │
│  └ decay_factor          (analysis 計算結果)                         │
└─────────────────┬─────────────────────────────────────────────────┘
                  ↓
┌──────────────────────────────────────────────────────────────────┐
│ Feature Engineering (src/features/builder.py — 52 列)               │
├──────────────────────────────────────────────────────────────────┤
│  base                  ← 4 テーブル JOIN                             │
│  + recent_form         ← 直近10走 (groupby + shift(1) + rolling)     │
│  + stadium_recent      ← 会場×選手 直近20走                          │
│  + course_recent       ← コース×選手 直近30走                        │
│  + motor_long_term     ← モーター直近50走 (会場固定)                  │
│  + weather_racer       ← 強風/高波時の選手別勝率                      │
│  + relative            ← レース内 6艇相対値・ランク                    │
│  + stadium_course_flag ← 進入コース崩れ・内枠フラグ                    │
└─────────────────┬─────────────────────────────────────────────────┘
                  ↓
┌──────────────────────────────────────────────────────────────────┐
│ Model Cascade                                                       │
├──────────────────────────────────────────────────────────────────┤
│  Stage 1: LightGBM Ranker (lambdarank)                              │
│    入力: 52 features × 6 boats   → raw_score                         │
│    + softmax → prob_first_uncalibrated                              │
│    + Isotonic Regression → prob_first / top_2 / top_3 (calibrated)  │
│                                                                     │
│  Stage 2: PerWinner LGBMClassifier × 6 (1着レーン別)                 │
│    入力: 候補艇の特徴量 + 1着艇の特徴量 + diff                        │
│    出力: P(2着 = 候補艇 | 1着 = X)                                  │
│                                                                     │
│  Stage 3: PerWinner LGBMClassifier × 6                              │
│    入力: 候補 + 1着 + 2着 + diff                                    │
│    出力: P(3着 = 候補 | 1着 = X, 2着 = Y)                           │
│                                                                     │
│  Joint:  P(A→B→C) = P(A=1着) × P(B=2着|A) × P(C=3着|A,B)             │
│                                                                     │
│  Joint Calibration: Isotonic で長い目過大評価を補正                   │
└─────────────────┬─────────────────────────────────────────────────┘
                  ↓
┌──────────────────────────────────────────────────────────────────┐
│ Inference / Evaluation Layer                                        │
├──────────────────────────────────────────────────────────────────┤
│  src/web/predictor.py     — モデルロード + キャッシュ + What-if        │
│  src/evaluation/                                                    │
│    ├ value_bet.py             — EV / Kelly 計算                      │
│    ├ value_bet_trifecta.py    — フル 120組 Value Bet (要 Layer 3)   │
│    ├ market_vs_model.py       — implied prob vs predicted edge       │
│    ├ subgroup_analysis.py     — 会場別/グレード別 ROI                 │
│    ├ bootstrap_ci.py          — ROI 95%CI (Bootstrap)                │
│    ├ venue_exclusion.py       — 戦略 ABCDE Bootstrap CI 比較         │
│    └ backtest.py              — Walk-Forward / single-split          │
└─────────────────┬─────────────────────────────────────────────────┘
                  ↓
┌──────────────────────────────────────────────────────────────────┐
│ Presentation Layer                                                  │
├──────────────────────────────────────────────────────────────────┤
│  Flask Web UI (src/web/app.py)                                      │
│    Public:                                                          │
│      / (date redirect) → /races?date=…                              │
│      /race/<race_id>     — 6艇予測 + 三連単 + What-if                │
│      /api/race/<id>/whatif POST — シミュレーション                    │
│    Member-only (パスワード認証):                                     │
│      /api/race/<id>/value-bets   — EV+ 検出                         │
│      /api/ev-races               — EV+ 一覧                         │
│                                                                     │
│  + 戦略バナー: 会場警告 ⚠ / SWEET SPOT ★                            │
│  + What-if Simulator: race_previews から DB 値プリフィル              │
└─────────────────┬─────────────────────────────────────────────────┘
                  ↓
              [End User Browser]
```

---

## 2. データモデル設計判断

### 2.1 race_id 規約: `YYYYMMDD-SS-RR`
- 同一文字列で全 Layer から参照可能（Open API・LZH・スクレイピング全部で同じ識別子）
- ソート可能（時系列クエリで `WHERE race_id LIKE '202604%'` で月単位抽出）
- パース容易: `race_id[:8]`, `race_id.split('-')`

### 2.2 テーブル分離設計

「**いつ取得可能か**」でテーブルを分けている：

| テーブル | 取得タイミング | データソース |
|---|---|---|
| `races`, `race_entries` | 前日 | Layer 1 / Layer 2 |
| `race_previews` | 直前 5-30分前 | Layer 2 / Layer 3 |
| `race_parts` | 直前 当日のみ | Layer 3 |
| `odds_trifecta` | 締切前後 | Layer 3 |
| `race_results`, `race_payouts` | 結果確定後 | Layer 1 / Layer 2 |

→ **学習時にリーク防止**を保証する物理的分離。`race_results` を学習特徴量に混ぜないコード書く時、テーブル単位で監視できる。

### 2.3 odds_trifecta の `snapshot_label`
2026-05 マイグレーションで追加。
- `T-1d`: 24h 前 (大きいレースのみ、SG/G1/優勝戦)
- `T-5min`: 5分前
- `T-1min`: 1分前
- `final`: 確定オッズ

**なぜ専用カラムか**:
- 同 (race_id, combination) で複数時点のオッズを保存可能 (PK = (race_id, combination, recorded_at))
- 減衰率分析で `T-5min` ↔ `final` ペア取得が SQL JOIN 一発
- インデックス `idx_odds_label` で snapshot 別検索が高速

### 2.4 SQLite + WAL の選択
**SQLite を選んだ理由**:
- ローカル運用、224K races 程度なら十分高速
- ファイル単位でバックアップ・移行容易
- スキーマ migration が単純

**WAL (Write-Ahead Logging) 必須**:
- スクレイパー (writer) + Web UI (reader) + scheduler (writer) が同時実行
- WAL モードなら read と write が並行可能
- `src/db/connection.py` で必須化 (PRAGMA journal_mode=WAL)

**移行先 Postgres にする時の判断基準**:
- データサイズ 1GB を超えたら (現状 ~150MB)
- 同時書き込みコネクション 5+ になったら
- RLS (Row Level Security) で会員ティア制御するなら

---

## 3. モデルアーキテクチャの哲学

### 3.1 なぜ「カスケード」か

ナイーブなアプローチ:
```
P(三連単 1-2-3) = LGBM で直接回帰
```
これは 120 出力の multi-class で、**データ希薄**（各組合せの正例 < 1%）になり学習困難。

**Plackett-Luce (古典的近似)**:
```
P(A→B→C) = P(A=1着) × P(B=1着 | A除外) × P(C=1着 | A,B除外)
```
シンプルだが「条件付き分布が単純な再正規化」と仮定。実際には「**1着が誰かで 2着分布が大きく違う**」という現実を捉えられない。

**カスケード（採用）**:
```
P(A→B→C) = P(A=1着) × P(B=2着 | 1着=A, features) × P(C=3着 | 1着=A, 2着=B, features)
```
各段階で**条件付き特徴量**（1着艇の class, 2連率, モーター等）を model に渡す。LightGBM が「1着が強い A1 選手なら 2着は 2/3号艇に偏る」のような複雑な交互作用を学習できる。

### 3.2 PerWinner（1着レーン別 6 モデル）

**ユーザー仮説**: 「1着が 1号艇」と「1着が 6号艇」では 2着・3着の傾向が**質的に**違う（前者は順当、後者は荒れた展開）。

**実装**:
```python
for w in [1, 2, 3, 4, 5, 6]:
    df_w = df_train[df_train.actual_winner == w]
    stage2_models[w] = LGBMClassifier().fit(df_w)
```

**結果**: PerWinner で Top-1 三連単 ROI **-9.97% → -7.05% (+2.92pt)**

**注意点**:
- 1着=6 のデータは少ない (v0.2 で n=4500)。過学習リスク
- 4年学習 (v0.4) で n=26K に増やすと overfit 解消するはず... だが**データドリフト**で逆に悪化
- 適切な学習期間は 2-3 年

### 3.3 Joint Calibration

**問題**: cascade の出力 `P(A→B→C)` は 120組合計が概ね 1.0 だが、各点の確率が**実頻度と乖離**する。特に長い目（1万倍オッズ等）。

**例**:
- model: `P(5-6-1) = 0.5%`
- 実頻度: 0.05%
- 10倍の overestimation

**解決**: `(predicted_prob, actual_hit)` ペアを **train データで集めて Isotonic Regression**:
- predicted = 0.005 → calibrated = 0.0006
- 単調マッピング (順位は保たれる)

これで EV 計算 `prob × odds - 1` の信頼性が上がる。

**現状**: 実装済 (`src/models/joint_calibration.py`)、各 cascade artifact に `joint_calib_*.pkl` が同梱される。

### 3.4 What-if Simulator のアーキテクチャ

```
[User] スライダーで風速 7→10 に変更
   ↓
[JS] gatherOverrides() → POST /api/race/<id>/whatif {overrides: {wind_speed: 10, "boat_3.tilt_adjustment": 3.0}}
   ↓
[Flask] predictor.predict_whatif():
   1. build_inference_frame(target_date) で 4列 JOIN ベースを再構築
   2. race_id でフィルタ → 6 行 DataFrame
   3. overrides を適用 (race-level は全行、boat-level は該当行)
   4. predict_probs() で Stage 1 走行
   5. apply_calibrators() で 1着確率較正
   6. predict_trifecta_per_winner() で Stage 2/3 + Joint
   7. JSON 返却
   ↓
[JS] DOM 更新 (6艇 prob テーブル + 三連単 Top-5)
```

**設計判断**:
- フロントエンドは現状 Vanilla JS（Flask テンプレ + fetch）。React 不要なシンプル UI
- バックエンド: 既存予測パイプラインを `predict_whatif` で同じ stack で再走行（special-case 無し）
- レース予測キャッシュとは別 cache_key (overrides hash) — 将来実装

---

## 4. Layer 別スクレイピング戦略

### 4.1 共通: rate-limited HTTP

```python
# src/collectors/_http.py
_lock = threading.Lock()
_last_request_at = 0.0

def fetch_html(url):
    with _lock:
        elapsed = monotonic() - _last_request_at
        if elapsed < 2.0:
            sleep(2.0 - elapsed)
        _last_request_at = monotonic()
    return requests.get(url, ...)
```

**プロセス内**でグローバル interval を保証。**並列禁止**。Celery 等で複数 worker 立てる時は queue 1つに集約必須（CLAUDE.md 記載）。

### 4.2 Layer 1 LZH パーサーの工夫

cp932 (Shift_JIS) 固定幅テキスト：
```
1 3773谷川 翔太郎49東京55B1 4.85 30.59 5.04 31.31 57 25.58 25 37.04 ...
```

- `splitlines()` で行分解、各行に regex
- 「会場マーカー」を `ボートレース<会場名>` で検出 → 24会場マスタ突合
- 「レースヘッダ」を `\d+R` で検出（全角→半角変換）
- 「艇行」を `^([1-6])\s+(\d{4})...` で検出
- regex は `compile()` してキャッシュ、ファイル全体 (~100KB) を 1秒以内で処理

### 4.3 Layer 3 odds パーサーの工夫

`odds3t` ページは `<table>` の中で **rowspan で 2着艇をまとめている**：
```html
<tr><td rowspan=4>2着=2</td><td>3着=3</td><td>11.0</td></tr>
<tr>             <td>3着=4</td><td>8.3</td></tr>
<tr>             <td>3着=5</td><td>8.5</td></tr>
<tr>             <td>3着=6</td><td>131.4</td></tr>
```

→ `_expand_rowspan()` で 2D 配列に展開してから組合わせ抽出。BeautifulSoup の rowspan 自動展開がうまく動かないため自前実装。

### 4.4 Layer 3 部品交換の制約

**重要な発見**: `boatrace.jp` は **過去日の部品交換 `<ul>` を空にする**。
```
2026-05-08 当日: <ul class="labelGroup1"><li>ピストン</li>...</ul>
2026-05-06 過去: <ul class="labelGroup1"></ul>  ← 空！
```

→ **当日中の自動取得が必須**。schtasks で 22:00 等にスナップショット。

### 4.5 odds_scheduler.py のロジック

```python
for race in races_today_or_tomorrow:
    close_time = parse(race.race_closed_at)
    for label, mins_before, tolerance in SNAPSHOT_RULES:
        target = close_time - timedelta(minutes=mins_before)
        if abs(now - target) <= tolerance:
            if (race_id, label) not in already_done:
                collect_one_race(race_id, snapshot_label=label)
```

- Cron で毎分起動
- 既に取得済みのレース×ラベルはスキップ
- 大きいレース (SG/G1/優勝戦) は T-1d (24h前) も取得

---

## 5. 評価アーキテクチャ

### 5.1 Time-based 3-way split (推奨)

```
[Train: 2022-05〜2024-12] [Val: 2025-01〜2025-09] [Test: 2025-10〜2026-05]
   モデル学習             ハイパラ・較正              開発で絶対触らない
```

**Test を絶対に触らない**ことが過学習バイアス排除の鍵。CLAUDE.md にも明記。

### 5.2 Walk-Forward Validation

時系列 CV：
```
窓 1: train [Y-12〜Y-1月] → val [Y月]
窓 2: train [Y-11〜Y月]   → val [Y+1月]
…
```

`backtest.py --strategy walk-forward --train-days 540 --val-days 30` で 24窓実行可能（4年データ揃った今）。

### 5.3 Bootstrap CI が必須

**点推定 ROI を信用しない**：
```python
for i in range(2000):
    sample = np.random.choice(bets, size=len(bets), replace=True)
    rois[i] = compute_roi(sample)
ci_lo, ci_hi = np.quantile(rois, [0.025, 0.975])
```

経験則: **n < 100 の subgroup は CI 巨大**。n=258 でも `[-40%, +70%]` レベル。

### 5.4 サブグループ分析

`subgroup_analysis.py` で:
- 会場別 (24)
- グレード別 (SG/G1/G2/G3/一般)
- 1号艇強度別 (<30%, 30-50%, 50-70%, 70%+)
- 風速帯別
- ナイター vs デイ
- 会場×強度クロス (ニッチ発見用)

**戸田 (-25.8% CI [-37%, -13%])** や **edge 50%+ (-52% CI [-65%, -38%])** など、**確実マイナスのゾーン切り**は信頼できる。

---

## 6. Web UI / 認証アーキテクチャ

### 6.1 Flask + Jinja2 (現状)

**シンプル路線**：
- Server-side render
- Vanilla JS で API 叩き (fetch)
- Tailwind 不使用（独自 CSS、~600 行）

**選択理由**:
- 個人開発・少数会員想定
- React/Next.js は overkill
- SEO 不要（会員制が前提）
- 開発スピード優先

**将来 SPA 化する場合**: Next.js + Supabase Client (CLAUDE.md のクラウド構想参照)

### 6.2 認証フロー (現状: シンプルセッション)

```
[Login Form] /login POST
  ↓ password 一致 → session["is_member"] = True
  ↓ Flask セッションクッキー (12h TTL)
  ↓ 以降のリクエストで is_member() で判定
[Member-only API]
  @member_only_api decorator → 401 unauthorized if not member
```

**制限**:
- 共有パスワード 1個 (個別ユーザー管理なし)
- HTTPS 必須（本番）— 平文セッションクッキー漏洩防止

**将来 (Supabase Auth)**:
- JWT ベース
- ロール (free / pro / admin) で RLS 自動判定
- メール認証・パスワード再発行

### 6.3 What-if API のキャッシュ戦略

```python
# 通常予測: 日付別キャッシュ (predict_date)
self._cache: dict[date_str, df]

# 三連単キャッシュ: race_id × mode 別
self._tri_cache: dict[(date, race_id, mode), list]

# What-if: キャッシュ無し (override が毎回違う)
predict_whatif() → 毎回 build_inference_frame() + 全段階予測
```

What-if のレスポンス時間 ~25秒は許容範囲（ユーザー操作が能動的）。リアルタイム表示用には predict_date のキャッシュ済みデータを使用。

---

## 7. 戦略レイヤー（Bootstrap CI 検証済）

### 7.1 確実な戦略（CI で確信）

```
□ 戸田・芦屋・三国・蒲郡 を切る
   Baseline (-9.6%) → 4会場除外 (-7.8%, +1.8pt)
   8会場除外 (-6.7%, +2.9pt)

□ edge 50%+ (モデル過信ゾーン) を切る
   ROI -52% (CI [-65%, -38%])

□ 1号艇 70%+ × 4会場除外 (Sweet Spot)
   ROI -3.06% (CI [-6.0%, -0.06%])
   → break-even 圏に到達
```

### 7.2 シグナルあり、確信は小（要追加データ）

```
△ 1号艇 30-50% (拮抗) × 限定会場
   桐生/丸亀/徳山/唐津で点推定 +20-25%
   CI 巨大 ([-22%, +89%])
   サンプル増 (60-90日 odds, 4年データ) で確認すべき
```

### 7.3 否定された戦略

```
✗ edge 10-25% で +3% ROI
   境界 1pt ずらすと -24% に転落 → Bootstrap CI [-40%, +70%]、配当外れ値依存

✗ パターン特徴量 (cascade-v0.2)
   P(2着=N | 1着=M @ stadium) を直接特徴量に → 既存特徴量との冗長で過学習悪化

✗ Optuna ハイパラ最適化 (v0.3)
   NDCG@1 微増 だが ROI 悪化
   → ランキング指標と財務指標は連動しない

✗ 4年データで再学習 (v0.4)
   ROI -28% に悪化
   → データドリフト (古いモーター番号、引退選手等)
```

---

## 8. ディレクトリ構造の役割分担

```
src/
├── collectors/       — 取得 (HTTP, ファイル DL)
├── parsers/          — 生データ → 構造化
├── db/               — DB 接続・migration
├── features/         — 特徴量エンジニアリング
├── models/           — 学習・推論・較正
├── analysis/         — 集計・統計分析 (decay_factor 等)
├── evaluation/       — 評価・バックテスト・CI
└── web/              — Flask UI / API / 認証

scripts/
├── daily_collect.py        — Layer 2 日次バッチ
├── scrape_layer3.py        — Layer 3 取得
├── backfill_official.py    — Layer 1 LZH バックフィル
├── train_*.py              — モデル学習
├── retrain_full_pipeline.py — 全モデル一括再学習
├── eval_*.py               — 評価系
├── odds_scheduler.py       — リアルタイムスナップショット
└── run_web.py              — Web UI 起動
```

**責務分離原則**:
- collectors/parsers は **生データを保存するだけ** — 解釈は features へ
- features は **学習・推論共通** で同じ DataFrame を返す (リーク防止)
- evaluation は **モデルに依存しない**（任意の prediction dict を入力として受ける）
- web は **既存 stack の薄いラッパー** — ロジックを web 内に書かない

---

## 9. クラウド移行先アーキテクチャ（構想）

```
┌─────────────────────────────────────────────────────────────┐
│ Cloudflare (CDN, DNS, WAF)                                    │
└──────────┬──────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────┐
│ Render.com                                                    │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │ FastAPI Web │  │ BG Worker    │  │ Cron Jobs          │  │
│  │ (推論API)    │  │ (Scrape)     │  │ - daily collect    │  │
│  │ + Pydantic  │  │ + RQ + interval保護 │  │ - odds T-5/T-1     │  │
│  └─────────────┘  └──────────────┘  │ - decay update     │  │
│        ↑ ↓             ↓            │ - retrain weekly   │  │
│   ┌────────┐    ┌──────────────┐    └────────────────────┘  │
│   │ Redis  │ ← ─┤ Cache (preds,│                             │
│   └────────┘    │ market data) │                             │
│                 └──────────────┘                              │
└──────────┬──────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────┐
│ Supabase                                                      │
│  ┌────────────────┐  ┌──────────────────────┐               │
│  │ PostgreSQL DB  │  │ Auth (JWT)           │               │
│  │ + RLS Policies │  │ - free / pro / admin │               │
│  │ - races, ...   │  │                      │               │
│  └────────────────┘  └──────────────────────┘               │
└─────────────────────────────────────────────────────────────┘
           ↑
┌─────────────────────────────────────────────────────────────┐
│ Stripe (Subscription)                                         │
│  Checkout → Webhook → FastAPI → Supabase user.role 更新       │
└─────────────────────────────────────────────────────────────┘
```

### RLS ポリシー設計例

```sql
-- 会員ロール別の予測アクセス制御
CREATE POLICY "predictions_pro_only"
ON predictions
FOR SELECT
USING (
  auth.jwt() ->> 'role' IN ('pro', 'admin')
  OR (auth.jwt() ->> 'role' = 'free'
      AND created_at < NOW() - INTERVAL '24 hours')  -- free は 24h 後のみ
);

-- T-5min/T-1min snapshot は pro 限定
CREATE POLICY "live_odds_pro"
ON odds_trifecta
FOR SELECT
USING (
  snapshot_label IN ('T-5min', 'T-1min')
  AND auth.jwt() ->> 'role' = 'pro'
);
```

---

## 10. パフォーマンス特性

### 10.1 データ規模 (現状)

| データ | 件数 | サイズ |
|---|---|---|
| races | 224,000 | ~30 MB |
| race_entries | 1,343,000 | ~80 MB |
| race_results | 1,258,000 | ~60 MB |
| race_payouts | 943,000 | ~30 MB |
| odds_trifecta | 240,000 (30日 + 一部) | ~10 MB |
| **DB合計** | | **~210 MB** |

Supabase Free Tier (500MB) には余裕あり。30日 × 全レース odds で +60MB/月見込み → **3-4ヶ月で Pro 移行**。

### 10.2 推論レイテンシ

| 操作 | 時間 | 備考 |
|---|---|---|
| Stage 1 単発 (1レース 6艇) | ~30ms | LightGBM predict |
| Stage 2/3 PerWinner (1レース 全120組) | ~1.5秒 | 30 model.predict_proba 呼出 |
| What-if 1レース | ~25秒 | + build_inference_frame で base 構築 |
| 全 144 レース cascade 予測 | ~4分 | キャッシュ前提でないと UI 不可 |

**最適化方向**:
- Redis キャッシュ (race_id 単位、TTL 10分)
- バッチ予測 (`predict_proba` を 30 個まとめて)
- 大きいレースの予測を事前に scheduler で計算

### 10.3 学習時間

| ステップ | 時間 (4年データ) |
|---|---|
| build_training_frame (52列) | ~3分 |
| Stage 1 Ranker | ~5分 |
| Stage 2 unified | ~3分 |
| Stage 3 unified | ~3分 |
| Stage 2 PerWinner ×6 | ~5分 |
| Stage 3 PerWinner ×6 | ~5分 |
| Joint calibration | ~10分 |
| **合計** | **~35分** |

retrain_full_pipeline.py で一括実行可能。週次自動 retrain を Render Cron で。

---

## 11. テスト戦略（未整備の課題）

### 現状欠けているもの

- [ ] **Unit test** (parser、feature builder、cascade prediction の数値)
- [ ] **Integration test** (E2E: scrape → DB → predict)
- [ ] **Regression test** (新モデルが既存モデルより悪化していないか)
- [ ] **CI pipeline** (GitHub Actions)
- [ ] **Schema migration test**

### 推奨実装順

1. **parser unit test** (既存 sample HTML を使った re-parse → 期待値突合)
2. **feature builder snapshot test** (固定 race_id でビルド結果のハッシュ確認)
3. **cascade smoke test** (任意レースで予測が non-NaN、合計確率 1.0±0.1)
4. GitHub Actions: pytest + flake8 + 型チェック (mypy)

---

## 12. 注意点・既知の罠

| 罠 | 対策 |
|---|---|
| LightGBM warning が stdout/stderr に大量出力 | `cascade._silence_native_stderr()` で OS-level fd dup |
| pandas groupby + apply で 1グループ時に DataFrame 返る | `add_top_k_uncalibrated` で手動 iterate に変更 |
| PowerShell の `*>>` redirect が UTF-16 LE | Python 側 `--log-file` で UTF-8 直書き |
| Start-Process で起動した子プロセスが親終了で死ぬ | `cmd /c start /B` か Background Worker 化 |
| pgrep で Windows プロセスが見つからない | DB 監視ベース waiter (race_id 到達でループ抜け) |
| race_closed_at が `HH:MM:SS` のみのケース | scheduler で race_date と合成 |
| Layer 3 部品交換が過去日で空 | 当日中に必ず取得 (schtasks) |
| 4年データで悪化 (data drift) | 2-3 年で再学習 |

---

## 13. ドキュメント間の役割分担

| ファイル | 対象読者 | 内容 |
|---|---|---|
| **README.md** | 利用者・初見の人 | What is this? / インストール / 使い方 |
| **CLAUDE.md** | AI アシスタント | 経緯・命名規約・プロジェクト固有のクセ・進捗 |
| **architecture.md** (本文書) | 設計を理解したい開発者 | なぜこの設計か / データフロー / 技術判断 |

---

## 14. 改訂履歴

- 2026-05-09: 初版作成 (4年バックフィル完了、PerWinner Cascade 実装、What-if 実装、認証実装、戦略バナー実装、Bootstrap CI 検証完了 のタイミング)
