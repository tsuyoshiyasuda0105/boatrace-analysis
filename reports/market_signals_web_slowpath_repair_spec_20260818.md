# 作業指示書: /api/market-signals Web経路の詰まり 恒久修理 (Codex CLI 用)

作成: 2026-08-18 夜 / 発注: リッキー / 診断・検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本 / 本番DBは Supabase Postgres)
テスト基準: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`

## 症状 (実データ)

会員が「アプリにアクセスできない」瞬間が発生する。サーバー自体は生存 (healthz ok,
通常時 0.15-0.45秒応答, DB 20/60) だが、**毎晩21-23時台に `/api/market-signals` が
230秒級**になり、gunicorn の同時8スロット (1 worker × 4 threads × 2 instance) を
食い潰して他リクエストを巻き添えにする。

| 日時 | slow_request 件数 | 最遅 |
|---|---|---|
| 8/16 22:58 | 551件 | 277.1秒 |
| 8/17 23:30 | 384件 | 254.7秒 |
| 8/18 21:23 | 313件 | 230.4秒 |

## 診断 (リンの仮説 — Codex は裏取りしてから直すこと)

- 本番 Web は `render.yaml` の startCommand で **`gunicorn ... 'src.web.app:create_app()'`**
  = 引数なし起動。`create_app(cached_predictions_only: bool = False)` の既定値が False。
- 一方 8/18 朝の修理 (85f2658) は `scripts/prewarm_strategy_pages.py` にだけ
  `create_app(cached_predictions_only=True)` を適用した。**Web 本体は従来のまま**。
- cron 側の signal_refresh は 08:00 以降 **30回すべて success** = cron 経路は直っている。
  つまり残る 230秒は **Web プロセス内で実行される市場シグナル計算**。
- `_effective_market_signals_recompute()` のガード自体は正しく実装されている
  (会員の `recompute=1` は EXPENSIVE_RECOMPUTE_TRIGGERS 以外では効かない) ので、
  **recompute とは別の重い処理**がキャッシュミス時に走っている可能性が高い。
  → **どこで 230秒使っているのかを計測で特定すること** (推測で直さない)。

## 絶対ルール (厳守)

1. **origin/main へ push 禁止・デプロイ禁止** (検品後にリンが実施)。
2. **本番DB (Supabase) への書込み・スキーマ変更をしない**。調査は読み取りのみ。
3. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を割らない。
4. **L4 判定結果を変えない** (CLAUDE.md「整合性を保つべきファイル群」)。出力内容は不変で、
   速度だけ直すのが本件のゴール。before/after で候補内容の一致を突合すること。
5. 作業ログ `reports/market_signals_web_slowpath_work_log_20260818.md`。
6. 稼働中の cron タスク構成を勝手に増減しない。

## やること

### [必須1] 230秒の内訳を計測で特定する (推測禁止)
- ローカルで `/api/market-signals?date=<今日>` を **キャッシュミス状態**から実行し、
  区間ごとの所要時間を計測 (関数単位のタイマ or cProfile)。
- 「どの関数/クエリが何秒か」を作業ログに数値で残す。ここが本件の肝。

### [必須2] Web 経路が重い計算をしないようにする
- 原則: **Web プロセスは保存済み結果 (predictions / last-good キャッシュ) を返すだけ**にし、
  重い全レース計算は cron 側 (既に成功している経路) に一本化する。
- 具体案 (計測結果に応じて Codex が最適な形を選ぶ):
  - `create_app()` の本番既定を cached-only 相当にする、または
  - market-signals ルートで重い経路に入らないようガードを追加し、キャッシュ無い時は
    last-good / 空+degraded フラグを即返す。
- **キャッシュが無い時に 230秒待たせない**こと (数秒以内に必ず応答)。

### [必須3] 出力の同一性を担保
- 修正前後で同一日付のシグナル内容 (候補レース集合・判定) が一致することを突合し、
  作業ログにハッシュか件数+race_id 一覧で証拠を残す。

### [必須4] 回帰テスト
- 「Web 経路では重い全レース計算を呼ばない」ことを保証するテストを追加
  (モデル/重い関数がWeb経路で呼ばれないことをモックで検証する等)。
- 既存 `tests/test_web_recompute_guard.py` の隣に置くのが自然。

### [任意5] 保険
- キャッシュミス時に degrade 応答を返す場合、UI 側が「更新中」と分かる表示になるか確認。

## 受け入れ条件

- [ ] 230秒の内訳が計測値として作業ログに記録されている
- [ ] Web 経路のキャッシュミス時応答が **数秒以内** になる (計測値で提示)
- [ ] シグナル出力 (L4候補) が修正前後で一致
- [ ] Web 経路が重い計算を呼ばないことの回帰テスト追加
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 / push なし / デプロイなし
- [ ] 作業ログに 計測 / 変更点 / 突合結果 / 残課題 / コミットID

## 検品 (リンが実施)

「計測で裏取りしたか」「Web経路が本当に軽くなったか」「L4出力が不変か」「push/デプロイ
していないか」を確認後、リンが本番へデプロイし、**当夜21-23時台に slow_request が
出ないこと**を実データで確認して発注者へ報告する。
