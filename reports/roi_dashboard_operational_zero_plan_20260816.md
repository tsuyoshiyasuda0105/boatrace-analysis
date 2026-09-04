# 作業指示書: ROIダッシュボードが実運用0になるバグの調査と修正 (Codex CLI 用)

作成: 2026-08-16 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ)
現行 main: 本番 `223b303`。テスト基準 `--ignore=tests/e2e --ignore=tests/round3_e2e`。

## 背景 (リンがコード・DBを照合して切り分け済み)

会員の ROIダッシュボード (`/member/strategy`, `member_strategy_v3.html`, 関数
`member_strategy` ~21289) が、集計範囲 2026-04-01〜08-16 で**すべて 0** を表示:
- ヘッダ: **実運用スナップショット 0日 / 再構築参考 138日**、投資 0円 / 該当 0件。
- 各戦略カード: n=0 的中=0 損益=+0。

### 仕組み (意図は正しい)
- 集計は「実際に『本日のレース』へ表示された確定スナップショット (operational)」だけを
  実運用 ROI に合算し、**再構築 (reconstructed) は参考扱いで合算しない** (後知恵バイアス防止)。
- 実運用判定 = 各日の `_adopted_from_market_signals_cache` が True。
  ソースは (a) `roi_race_history` 台帳 (`load_roi_history_daily`)、
  (b) `market_signals` last-good/current 確定スナップショット。

### リンが確認した「実運用データは実在する」事実
- **`roi_race_history` 台帳に 187 行 / 38 日分 (2026-07-02〜08-16) の実運用データが存在**。
  `strategy_key` (例 `a1_ace_motor_123_corr_tri`, `g23_optb_tri`,
  `tamagawa_123_fl3_n3_30_m2_35_tri`)、`stake_amount` / `payout_amount` / `is_hit` /
  `is_settled=1` / `capture_quality='live_last_good'` / `source_cache_key='market_signals:last-good:<date>'`
  が入っている。`updated_at` は最新で 2026-08-16 19:58。
- `market_signals:last-good:<date>` 確定スナップショットも 21 日分 (07-12〜08-16) 実在。
- **`_l4_daily_stats` と `_l4_daily_stats_cache_only` の両方に、台帳を overlay して
  `_adopted_from_market_signals_cache=True` を立てるコードが既にある** (cache_only は
  15990-16088、full は 16090-)。

### つまりバグ
実運用データが 38 日分あり、overlay コードもあるのに、**ダッシュボードは実運用 0 日**。
overlay が実際の表示結果に反映されていない。**設計ではなく実装/経路のバグ。**

## ゴール

**`roi_race_history` 台帳 (と確定スナップショット) の実運用データが、ROIダッシュボードの
実運用集計 (投資/払戻/回収率/損益/実運用日数) に正しく反映される**ようにする。
再構築を実運用に混ぜない設計 (後知恵バイアス防止) は**維持**する。

## 絶対ルール

1. **origin/main へ push 禁止** (ローカル main まで)。
2. **ROI の集計定義・戦略ロジック・後知恵バイアス防止の設計は変えない**。実運用と
   再構築の分離は維持。台帳の値をそのまま正しく表示に通すのが目的で、数字を
   でっち上げない・再構築を実運用に混ぜない。
3. DB スキーマ・予測・render.yaml・収集ロジックは変更しない。
4. `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e` を割らない + 新規 green。
5. 作業ログ `reports/roi_dashboard_operational_zero_work_log_20260816.md`。コミット1〜3個。

## やること

### 1. なぜ 0 になるかを実際に走らせて特定する

`member_strategy` の経路を追い、**overlay が服務結果に届かない原因**を突き止める。
容疑 (いずれか/複数):
- **stale な HTML ページキャッシュ**: `member_strategy` は
  `_read_page_html_cache(page_cache_key)` がヒットすると**そのまま古い HTML を返す**
  (overlay を実行する `_l4_daily_stats_cache_only` すら呼ばれない)。台帳が後から
  埋まっても、TTL 内は 0 のままの HTML を配信し続ける可能性。
- **`load_roi_history_daily` が実行時に空を返す**: 引数 (ROI_STRATEGY_KEYS)・日付・
  strategy_key・is_settled/is_active 等の条件で 0 件になっていないか。台帳の
  strategy_key と `ROI_STRATEGY_KEYS` の突き合わせを確認。
- **overlay 例外の握り潰し**: cache_only の overlay は try/except で
  `"ROI race history cache-only overlay failed"` を warning に落として続行する。
  ここで例外が出ていないかログ/実行で確認。
- **月別 (`member_strategy_monthly`) 側も同様に 0 でないか**確認。

**実際に該当関数を呼び、38日が operational として数えられるか**を検証してから直す。

### 2. 実運用データが表示に反映されるよう修正

- **台帳が更新されたら実運用集計が反映される**ようにする。stale HTML キャッシュが
  原因なら、台帳更新 (roi_race_history) を考慮したキャッシュ無効化/短縮、または
  ページキャッシュのキーに台帳の最新性を織り込む等で、**古い 0 表示を配信し続けない**。
- `load_roi_history_daily` の突き合わせにバグがあれば直す (キー/日付/settled 条件)。
- **overlay が正しく operational フラグを立て、`operational_day_count` と実運用 ROI
  (投資/払戻/回収率/損益) に反映される**ことを保証。
- 「通常表示は軽量に保つ」既存方針 (web worker で重い SQL を走らせない) は尊重する。
  台帳 overlay は軽いクエリなので通常表示でも実行してよい。

### 3. 数字の正しさ

- 反映された実運用 ROI が台帳 (stake/payout/is_hit) と一致すること。
- 再構築日は引き続き「参考」に留まり、実運用合算に混ざらないこと。
- 未確定 (is_settled=0) を実運用実績に含めないこと (既存ガード維持)。

## テスト (`tests/` に追加)

- 台帳に実運用データがある日が **operational として集計**され、投資/払戻/損益が台帳と
  一致することを fake DB / フィクスチャで検証。
- 台帳更新後に古いキャッシュ 0 を配信し続けない (キャッシュ無効化/反映の検証)。
- 再構築のみの日は実運用合算に入らない (後知恵バイアス防止の維持)。
- `load_roi_history_daily` が実データ相当の strategy_key/日付で非空を返す回帰。

## 受け入れ条件

- [ ] 台帳の 38 日 (7/2〜8/16) 実運用データがダッシュボード実運用集計に反映される
- [ ] 投資/払戻/回収率/損益/実運用日数が台帳と一致 (0 でなくなる)
- [ ] 再構築を実運用に混ぜない設計を維持 (後知恵バイアス防止)
- [ ] 通常表示が重い SQL に落ちない方針を維持
- [ ] `pytest ... --ignore=e2e --ignore=round3_e2e` 維持 + 新規 green / push なし / 作業ログ

## 検品 (リンが実施)

「台帳の実運用データが実運用集計に出るか (0 でなくなるか)」「数字が台帳と一致するか」
「再構築を混ぜていないか (後知恵バイアス防止維持)」「stale キャッシュを配信し続けないか」
「通常表示が重くなっていないか」「テスト green か」を照合。デプロイは発注者承認後。
