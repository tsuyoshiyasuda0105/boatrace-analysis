# 作業指示書: DB 接続プールの自己修復とフェイルファスト (Codex CLI 用)

作成: 2026-08-15 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: **802 passed** / 直前コミット `1c4cf70` (graceful degradation)。

## 背景 (2026-08-15 夜の本番ログで確定した事実)

Render web インスタンス `[ltp54]` が **30分以上ずっと**同じ状態でエラーを出し続けた:

```
postgres pool checkout failed stats={
  'requests_num': 50, 'requests_queued': 48, 'requests_errors': 47,
  'connections_num': 2, 'connections_lost': 1, 'connections_ms': 850,
  'pool_min': 1, 'pool_max': 8, 'pool_size': 2, 'pool_available': 0,
  'requests_waiting': 47, 'requests_wait_ms': 235907 }
```

読み取れること:
1. `pool_max: 8` は効いている (環境変数は反映済み)。
2. しかし **`pool_size: 2` のまま増えない**。`connections_num: 2` = このプロセスで
   これまでに**2本しか接続を作れていない**。
3. その2本が `pool_available: 0` = **返却されず占有されたまま**。
4. `requests_waiting` が 14 → 47 と**単調増加**し、待ち行列が永久に伸びる。
5. 結果: `/member/today-races` などが 500 を返し続け、**再起動するまで直らない**。

**本質**: 一度この状態に落ちると**インスタンスが自力で復旧できない**。
これが「頻繁にエラーになる」の正体 (実際は「一度壊れたらずっと壊れたまま」)。

なお `src/web/app.py` の `db_connect()` 利用箇所は `owns_connection` +
`try/finally` で閉じており、**単純な閉じ忘れは見つからなかった**。原因は
プール自体の回復不能状態と、待ち行列の無制限な蓄積にある。

## ゴール

1. **待ち行列を無制限に伸ばさない** (フェイルファスト)。
2. **プールが壊れたら自力で作り直す** (自己修復)。再起動を待たない。
3. 上記が起きた事実を**記録**して、後から頻度を検証できるようにする。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. ROI 戦略・予測・DB スキーマ・render.yaml は変更しない。
3. **cron/スクリプト側の挙動を壊さない**。`src/db/connection.py` は web と全 cron が
   共有する。長時間バッチが誤って中断されないよう、**web とバッチで設定を分けられる**
   ようにする (既存の `BOATRACE_TASK_TRIGGER` で区別する流儀に合わせる)。
4. `.venv/Scripts/python.exe -m pytest tests/ -q` — **802 passed を割らない**。
5. 作業ログ `reports/db_pool_selfheal_work_log_20260815.md`。コミット2〜3個。

## やること

### 1. 待ち行列の上限 (max_waiting) — フェイルファスト

`ConnectionPool` に **`max_waiting`** を設定する (psycopg_pool の機能)。
- web (通常リクエスト) では待ち行列を短く (例: 既定 `pool_max` 相当〜十数件)。
  上限超過時は `TooManyRequests` 系の例外が**即座に**返る → 47件も溜まらない。
- 上限は環境変数で調整可 (`BOATRACE_DB_POOL_MAX_WAITING` 等)。**0/未設定で無制限**に
  ならないよう、web では必ず有限の既定値を持たせる。
- **バッチ/cron では無制限または大きめ**にしてよい (長時間ジョブを殺さない)。
  `BOATRACE_TASK_TRIGGER` の有無で既定値を分ける。
- 溢れた例外は `is_transient_db_error()` が **一時エラーとして扱う**ようにする
  (既存の graceful degradation が古いキャッシュへ退避できる)。

### 2. プールの自己修復 (watchdog)

「**プールが枯渇したまま回復しない**」状態を検知して、**プールを作り直す**:
- 判定条件の例: `pool_available == 0` かつ `requests_waiting > 0` かつ
  その状態が **一定時間 (既定 60〜120秒) 継続**、かつ直近の checkout が失敗している。
  → `_PG_POOL` を close して None に戻し、次回接続時に**新しいプールを作る**。
- **誤爆防止が最重要**: 正常な高負荷 (一時的に available=0) で作り直さないこと。
  必ず「継続時間」と「失敗の実績」を条件に含める。
- 再作成は**プロセス内で1回ずつ**、連続再作成を防ぐクールダウン (例: 60秒) を設ける。
- 再作成時は必ず**ログを出す**。
- 既存の `_PG_POOL_LOCK` を使い、スレッド安全に行う。

### 3. 事実の記録

プール枯渇・自己修復の発生を、既存の仕組みで軽く記録する。
- `src/web/app.py` の既存 `_note_transient_db_error` (直前コミットで追加) を活用するか、
  接続層からは**ログのみ**にして、web 側で記録する形でもよい。
- **新テーブルは作らない**。記録失敗が本流を止めないこと。

### 4. (任意・低リスクなら) 統計の可視化

`/admin/data-status` など既存の管理画面に、直近のプール統計 (pool_size /
available / waiting / 最終自己修復時刻) を**軽量に**表示できると運用が楽になる。
DB を増やして引かないこと。**難しければ省略可** (作業ログに理由を書く)。

## テスト (`tests/` に追加)

- 待ち行列が上限に達したら**即座に**エラーになる (無限に待たない)。
- その例外が `is_transient_db_error()` で一時エラーと判定される。
- 枯渇状態が継続すると watchdog がプールを作り直す (fake pool で検証)。
- **一時的に available=0 になっただけでは作り直さない** (誤爆防止の検証)。
- クールダウン中は連続で作り直さない。
- `BOATRACE_TASK_TRIGGER` 有り (cron) では待ち行列制限が緩い/無制限。
- 既存の接続再試行・graceful degradation の回帰がない。

## 受け入れ条件

- [ ] 待ち行列が無制限に伸びない (テストあり)
- [ ] 枯渇が続いたらプールが自動再生成され、再起動なしで復旧する (テストあり)
- [ ] 正常な高負荷で誤爆しない (テストあり)
- [ ] cron/バッチの長時間ジョブを壊さない (設定分離、テストあり)
- [ ] `pytest tests/ -q` 802 passed 維持 + 新規 green / push なし / 作業ログ

## 検品 (リンが実施)

「誤爆防止の条件が十分か」「cron を巻き込まないか」「無限待ちが本当に消えるか」
「既存機能の回帰がないか」「全テスト green か」を照合。デプロイは発注者承認後。
