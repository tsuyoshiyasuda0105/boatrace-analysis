# Boatrace Analysis

> 競艇 (公営ボートレース) の機械学習による予測と期待値 (EV) 可視化システム。
> **正直な数字を扱うツール**であり、利益保証ではありません。

[![CI](https://github.com/tsuyoshiyasuda0105/boatrace-analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/tsuyoshiyasuda0105/boatrace-analysis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

---

## 何ができるか

| 機能 | 内容 |
|---|---|
| **1着確率予測** | LightGBM Ranker (v0.8) で 6艇の 1着確率を出力 |
| **三連単 joint 確率** | PerWinner Cascade (lane別6モデル) で 120 組合せの確率 |
| **期待値 (EV)** | モデル確率 × オッズ - 1 を全レース表示 (free でも閲覧可) |
| **What-if シミュレーター** | 風速・波高・展示T/ST 等を変えてリアルタイム再予測 |
| **会場警告** | Bootstrap CI で確実マイナスと判明済みの 4 会場を警告 |
| **Pro: T-15min EV モニター** | 締切 15分前オッズで 144 レース横断の EV ランキング |

## 重要な事実 (検証で判明)

- 公営ボートレースの **控除率は 25%**。長期 EV は構造的にマイナスです。
- 我々のリーク無し test set (n=668-750) でも:
  - Sweet Spot 戦略 (1号艇70%+ × 4会場除外): **ROI -3.66% [-7.6%, +0.4%]**
  - 全レース固定買い: ROI **-11.5%**
- 「市場非効率な領域で +EV を狙う」アプローチは試したが、現状 **継続的 +EV は達成不可** という結論。
- 詳細は [architecture.md](architecture.md) の検証ログ参照。

**このツールは「賢く負けないため」「競艇予測の数字を理解するため」の支援ツールです。**

---

## クイックスタート

### 1. 環境準備

```bash
# Python 3.10+ 推奨
python -m venv .venv
source .venv/bin/activate          # Linux/Mac
# .venv\Scripts\activate            # Windows PowerShell

pip install -r requirements.txt
```

### 2. 環境変数

```bash
cp .env.example .env
# 内容を編集 (本番では必ずパスワード/シークレットを変更)
```

### 3. データ取得

```bash
# DB 初期化
python scripts/init_db.py

# 当日データ取得 (Open API)
python scripts/daily_collect.py

# 過去 4 年分のバックフィル (Layer 1, 7-Zip 必要)
python scripts/backfill_official.py --date-from 2022-04-01 --date-to 2026-03-31
```

### 4. モデル学習

```bash
# Stage 1 Ranker (recency weighted)
python scripts/train_ranker_recency.py \
    --date-from 2022-04-01 --date-to 2026-03-31 \
    --decay-half-life-days 180 --version v0.8

# Stage 2/3 Cascade (PerWinner)
python scripts/retrain_full_pipeline.py --version v0.8
```

### 5. Web UI 起動

```bash
python scripts/run_web.py --port 5050
# http://127.0.0.1:5050 へアクセス
```

---

## アーキテクチャ

```
┌──────────────────────────────────────────────────────────┐
│ Layer 1: 公式 LZH ファイル (番組表 / 競走成績)              │
│ Layer 2: Boatrace Open API (JSON, 日次自動取得)            │
│ Layer 3: 公式サイト スクレイピング (オッズ T-15/T-5/final)  │
└────────────────────┬─────────────────────────────────────┘
                     ↓
                SQLite (WAL mode)
                     ↓
        src/features/builder.py で 61 特徴量化
                     ↓
   ┌─────────────────────────────────────┐
   │ Stage 1: LightGBM Ranker            │
   │   → Isotonic 較正 → 1着確率          │
   ├─────────────────────────────────────┤
   │ Stage 2: P(2着|1着=w)  (6 モデル)    │
   │ Stage 3: P(3着|1,2着)  (6 モデル)    │
   │   → joint trifecta 120組合せ         │
   ├─────────────────────────────────────┤
   │ Joint Calibration (Isotonic)         │
   │   → calibrated joint probability     │
   └─────────────────────────────────────┘
                     ↓
              Flask Web UI
              (free / Member / Pro 階層)
```

詳細は [architecture.md](architecture.md) を参照。

---

## モデル系譜

| バージョン | 内容 | NDCG@1 | Test set ROI (Sweet Spot) |
|---|---|---|---|
| v0.1 | 365日学習、recent_10 のみ | — | — |
| v0.2 | + パターン特徴 (悪化) | — | — |
| v0.4 | 4年学習 (drift で悪化) | — | — |
| v0.5 | 2年学習 + 較正 | — | — |
| **v0.6** | recency 加重 (half-life 180d) | 0.6878 | -4.68% [-8.9%, -0.6%] |
| v0.7 | + recent_30/50 long form | 0.6879 | -5.36% (改善せず) |
| **v0.8** | + 選手×会場/コース 100走長期 | 0.6874 | **-3.66% [-7.6%, +0.4%]** |

v0.8 は CI 上限が初めて 0% を超えた (P>0=4%) モデルですが、依然として継続的 +EV は保証されません。

---

## ディレクトリ構造

```
boatrace-analysis/
├── config.py               # 設定 (環境変数で上書き可)
├── requirements.txt
├── .env.example            # 環境変数テンプレート
│
├── src/
│   ├── collectors/         # データ取得 (Layer 1/2/3)
│   ├── parsers/            # LZH パース (Shift_JIS)
│   ├── features/builder.py # 特徴量生成
│   ├── models/             # LightGBM Ranker / Cascade
│   ├── evaluation/         # Bootstrap CI / Walk-Forward / niche scanner
│   └── web/                # Flask UI
│
├── scripts/                # 実行スクリプト
│   ├── train_ranker_recency.py
│   ├── odds_scheduler.py   # T-15/T-5/T-1 取得 cron
│   └── daily_collect.py
│
├── tests/                  # pytest
└── notebooks/              # 分析メモ (gitignore)
```

---

## デプロイ

公開向けデプロイ手順は [DEPLOY.md](DEPLOY.md) を参照。

主な選択肢:
- **Render / Railway**: Flask + SQLite (個人利用)
- **VPS + Cloudflare Tunnel**: Pro 有料化、会員管理を本格化する場合
- **GitHub Pages**: 不可 (Python ランタイム必要)

---

## ライセンス & 免責

[MIT License](LICENSE) で公開しています。

**本ツールは投資 / 利益保証ツールではありません。**
公営競技は控除率 25% で長期 EV は数学的にマイナスです。検証データに基づく判断材料の提供を目的としており、
本ツールの利用によって生じた損失について作者は一切責任を負いません。

ギャンブル依存にお悩みの方は、ぜひ [ギャンブル等依存症対策ポータルサイト](https://www.gamblingaddiction.jp/) や専門相談窓口にご相談ください。

---

## Contributing

Issue / PR 歓迎。特に以下の領域:
- 競輪・オートレースへの拡張 (同じ控除率 25% で類似アプローチ可能)
- 市場特徴量 (オッズドリフト、smart money 検出)
- UI/UX 改善
- データ取得パイプラインの安定化

開発時のテスト実行:

```bash
pytest tests/
```
