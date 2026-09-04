# 作業指示書: ゲスト開放 + ベータ会員ロール新設 (Codex CLI 用)

作成: 2026-08-18 深夜 / 発注: リッキー / 診断・検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本 / 本番は Render + Supabase Postgres)
テスト: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`
(現状 1098 passed, 1 skipped。これを割らないこと)

## ゴール (発注者が指定した権限表)

| 層 | ロール | 見られるもの |
|---|---|---|
| ゲスト | 未ログイン | TOP / レース一覧 / 個別レース情報 |
| ベータ会員 | `beta_member` (新設) | 上記 + **バックテスト** |
| 有料会員 | `paid_member` | 上記 + **バックテスト** |
| 管理者 | `admin` | すべて |

既存の `free_member` は現状維持 (ログイン会員として本日のROI候補などを見る層)。
ROI / 月別推移 / 健全度 / 事故率 / 展示精度 / 管理 は **admin 限定のまま**変更しない。
「本日のROI候補」(`/member/today-races`) は **会員限定のまま**でゲストに出さない。

## 現状 (調査済み・前提として使ってよい)

- `src/web/membership.py`: `ROLE_RANK = {"guest": 0, "free_member": 10, "paid_member": 20, "admin": 100}`
  `role_allows(role, required)` は **ランク >= 比較**。
- `src/web/auth.py`: `_SUPABASE_MEMBER_ROLES = frozenset({"free_member", "paid_member", "admin"})`
  同じ内容の set リテラルが `session["is_member"] = role in {...}` で 2 箇所ある (計 4 箇所)。
- バックテスト: `src/web/kachisuji_bp.py` が `is_paid_member()` で門番。
- ゲストに閉じているページ: `src/web/app.py` の `@app.route("/")` (7488),
  `@app.route("/races")` (7498), `@app.route("/race/<race_id>")` (7748) が `@login_required`。

## 設計上の必須判断 (ここを間違えないこと)

**ランク階層だけでベータを表現しない。** `beta_member` を paid(20) より下に置くと
`role_allows(role, "paid_member")` が False になり、逆に paid より上に置くと
有料限定機能までベータに開いてしまう。**機能単位の許可関数**を導入すること:

```python
def can_use_backtest() -> bool:
    return current_role() in {"beta_member", "paid_member", "admin"}
```

`ROLE_RANK` には `beta_member: 15` を追加 (free < beta < paid の序列自体は自然)。
バックテストの門番だけ上記の機能関数に差し替える。

## やること

### [必須1] ベータ会員ロールの新設
- `ROLE_RANK` に `beta_member: 15`。
- `_SUPABASE_MEMBER_ROLES` と `session["is_member"] = role in {...}` の **4 箇所すべて**に
  `beta_member` を追加 (片方だけ直すとログイン直後だけ会員扱いされない不整合になる)。
- `normalize_role` が `beta_member` を潰さないこと。

### [必須2] バックテストの門番を機能ベースへ
- `can_use_backtest()` を追加し、`src/web/kachisuji_bp.py` の `is_paid_member()` 判定を差し替える。
- API 側 (`_paid_member_api_forbidden` 相当) も同じ関数を使うこと。**画面とAPIで判定がズレないこと。**

### [必須3] ゲスト開放 (TOP / レース一覧 / 個別レース情報)
- `/`, `/races`, `/race/<race_id>` の `@login_required` を外し、未ログインでも 200 を返す。
- ヘッダーはゲスト時に会員メニューを出さない (既存の `{% if is_member() %}` で担保されているはず。要確認)。
- **ゲストに会員限定情報を漏らさないこと**: ROI候補バッジ・EV+マーク・採用ROI戦略ラベルなど、
  会員向けに出している要素がゲスト画面に出ていないかテンプレートを実際に確認する。

### [必須4] ゲスト経路で重い計算を絶対に走らせない (最重要・今日の障害の教訓)
本日 (2026-08-18)、`/api/market-signals` が Web プロセス内で全日再計算していたため
**42秒 (SQL 212本)、本番では 230秒**に達し、gunicorn のスロットを食い潰して
サイト全体が数分間開けなくなる障害が発生した (修理コミット `cba5028`)。
**公開範囲を広げる本件は、その再発リスクを桁違いに拡大する。**

- ゲスト経路は**保存済み/キャッシュ済みデータのみ**を返すこと。キャッシュミス時に
  モデル推論や全レース走査に入ってはならない。入りそうな箇所は計測で確認する。
- 個別レース (`/race/<race_id>`) はキャッシュミス時の実測所要を作業ログに数値で残すこと。
  重い場合は簡易表示 (保存済み予測のみ) にフォールバックする。

### [必須5] 混雑時にゲストを閉じるスイッチ
- 環境変数 `BOATRACE_GUEST_ACCESS` (既定 "1" = 開放、"0" = 従来どおりログイン必須) を追加。
- **再デプロイなしで Render の環境変数変更だけで閉じられる**こと (プロセス起動時に一度読むのではなく、
  リクエスト時に評価する。ただし毎リクエストの os.getenv は安価なので問題ない)。

### [必須6] テスト
- ゲストで `/`, `/races`, `/race/<id>` が 200
- ゲストで `/member/today-races`, `/kachisuji/`, ROI系 が 302 or 403
- `beta_member` でバックテストが 200、ROI系は不可
- `free_member` でバックテストが不可
- `BOATRACE_GUEST_ACCESS=0` でゲストがログインへ転送される
- ゲスト画面に会員限定要素が出ていない

## 絶対ルール

1. **origin/main へ push 禁止・デプロイ禁止** (検品後にリンが実施)。
2. **本番 Supabase への書込み・スキーマ変更をしない**。調査は読み取りのみ。
   (`beta_member` の付与運用は別途。コードは値を受け付けられれば良い)
3. 既存テスト 1098 passed を割らない。
4. admin 限定ページの範囲を変えない。
5. 稼働中の cron 構成を増減しない。
6. 作業ログ `reports/guest_access_beta_role_work_log_20260818.md` に
   変更点 / ゲスト経路の実測 / テスト結果 / 残課題 / コミットID。

## 受け入れ条件

- [ ] 権限表どおりに動作し、テストで保証されている
- [ ] ロール定義の 4 箇所すべてに beta_member が入っている
- [ ] 画面とAPIでバックテスト判定が一致
- [ ] ゲスト経路がキャッシュ済みのみを返す (実測値を作業ログに記載)
- [ ] `BOATRACE_GUEST_ACCESS=0` で即座に閉じられる
- [ ] ゲスト画面に会員限定情報が出ていない
- [ ] pytest 1098+ passed / push なし / デプロイなし
