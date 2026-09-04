# 作業指示書 第2段階: 公開範囲の変更 (Codex CLI 用)

作成: 2026-08-22 / 発注: リッキー / 検品: リン (Claude)
**前提: 第1段階 (reports/public_access_phase1_spec_20260822.md) が本番反映済みであること。**
守りを固める前に門を開けないこと。

## 目的 (発注者方針)

| 区分 | 対象 | 認証 |
|---|---|---|
| 公開 | レース一覧 / **レース詳細 (予測含む)** / ROI公開ページ | 不要 |
| 会員 | **本日のレース (/member/today-races)** / **バックテストLAB (/kachisuji)** | Supabase ログイン |
| 管理者 | 月別推移 / 事故率 / 健全度 / ST展示精度 / 管理 | 現状維持 (@admin_required) |

## やること

### [必須1] レース詳細を公開にする
`/race/<race_id>` とその表示に必要な API を認証不要にする。
対象の判断はコードを読んで決めること。少なくとも以下は詳細ページの描画に
使われているので、公開側に合わせる必要があるか確認する:
- `/api/race/<race_id>` / `/api/race/<race_id>/signals`
- `/api/race/<race_id>/motor-history/<boat>` / `.../racer-detail/<boat>`

**ただし `/api/race/<race_id>/value-bets` は会員限定のまま**
(EV/Value Bet は会員価値の中核)。

### [必須2] 会員限定を維持する対象
- `/member/today-races` と `/member/today-races/history`
- `/kachisuji` 配下すべて
- `/api/member/*` / `/api/market-signals` / `/api/odds-123-timeline`
判断に迷うものは**会員側に倒す** (後から公開する方が、誤って公開するより安全)。

### [必須3] 会員判定を Supabase 前提に整理
公開が広がる分、会員判定が通る条件を狭める。`is_member()` が
共有パスワード由来のセッションでも真になる現状は第3段階で廃止するが、
本段階では**判定箇所を増やさない**こと。

### [必須4] 画面の導線
公開ページから会員限定ページへのリンクは残してよいが、未ログインで踏んだ時に
403 の素の画面ではなく、既存のログイン誘導へ飛ぶこと (現行の `login_required`
の挙動を踏襲)。

### [必須5] 回帰テスト
- 未ログインで `/race/<id>` が 200 を返し、予測が含まれること
- 未ログインで `/member/today-races` と `/kachisuji` がログインへ誘導されること
- 未ログインで `/api/race/<id>/value-bets` が 403 相当であること
- 管理者専用ページが引き続き管理者のみであること
- レース詳細のキャッシュキーに役割が混ざらないこと (公開・会員で同じHTML)

## 絶対ルール
- push 禁止・デプロイ禁止・本番 Supabase 書込み禁止
- 採用ROI戦略の判定結果を変えない / render.yaml を変更しない
- 展示データの反映内容を減らさない
- レース詳細の描画経路 (fresh/stale キャッシュ、背景再生成の同時1本上限) を
  変更しない。**今日それで本番を不安定にしたため触らないこと**
- 作業ログ: reports/public_access_phase2_work_log.md
