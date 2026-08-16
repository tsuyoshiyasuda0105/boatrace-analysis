# ROIダッシュボード実運用0 修正作業ログ

作業日: 2026-08-16

対象: `/member/strategy`, `/member/strategy/monthly`

作業ブランチ: ローカル `main`（pushなし）

## 結論

`load_roi_history_daily` と `_l4_daily_stats_cache_only` の台帳overlayは正常だった。0表示の根本原因は、ROI台帳の更新を知らないHTMLキャッシュがルート先頭で返され、overlayまで処理が到達しないことだった。

- Flaskの `@cached(ttl=600, past_ttl=7200)` がルート関数自体を迂回する。
- DBの `page_html_cache` は戦略定義・期間だけをキーにし、`roi_race_history` の更新を含めていなかった。
- fresh期限切れ後も同じキーのstale HTMLを無条件で返すため、台帳が後から埋まっても古い0または部分集計を返し続け得た。
- 調査時の本番保存HTMLも、台帳最終更新前に生成された「29日・投資15,700円・払戻12,930円」の部分集計だった。一方、同時点の実関数overlayは38日を返した。

overlay例外、戦略キー不一致、`load_roi_history_daily` の空返却は再現しなかった。本番台帳のstrategy_keyは94件の現行レジストリにすべて含まれ、不一致は0件だった。

## 実データによる原因切り分け

本番Supabaseへ短命な直接接続を使い、すべてSELECT専用で確認した。ローカルPCから本番スケジューラやwriterは起動していない。

対象期間 `2026-04-01`〜`2026-08-16`:

| 段階 | 行数 | 日数 | 備考 |
|---|---:|---:|---|
| 台帳raw | 187 | 38 | 発注時に示された全行 |
| `is_settled=1` | 183 | 38 | 未確定4行を除外 |
| settledかつ`is_active=1` | 174 | 38 | 非active 9行をさらに除外 |
| 既存の女性除外/混在ガード適用後 | 171 | 38 | 既存ガード対象3行をさらに除外 |

`load_roi_history_daily` の実行結果:

- 38日（2026-07-02〜2026-08-16）
- 171件
- 的中28件
- 投資17,500円
- 払戻14,350円

修正前コードの `_l4_daily_stats_cache_only('2026-04-01', '2026-08-16')` を本番読取接続で直接実行した結果も、38日・171件・的中28件・投資17,500円・払戻14,350円だった。したがって台帳loader/overlayではなく、ルート手前のHTMLキャッシュ早期returnが原因と確定した。

## 修正内容

`src/web/app.py`:

1. `_roi_history_page_revision(from_date, to_date)` を追加。
   - `roi_race_history` に対して期間内の `MAX(updated_at), COUNT(*)` のみを読む。
   - `is_settled=1 AND is_active=1` を維持。
   - 日付indexを使える小さな集計で、重い過去再構築SQLは実行しない。
2. 日別・月別HTMLキャッシュキーに台帳revisionを追加。
   - 台帳更新後は別キーになり、古い0/部分HTMLを返さずcache-only overlayへ進む。
3. 日別・月別ルート外側の `@cached` を除去。
   - 外側キャッシュがrevision計算自体を迂回する問題を解消。
   - 既存のDB/メモリHTMLキャッシュは維持し、通常表示の軽量方針を維持。
4. 既存の実運用合算を `_operational_roi_totals` に純関数化。
   - `_adopted_from_market_signals_cache=True` の行だけを合算する定義は不変。
   - reconstructed行は引き続き参考件数だけで、投資・払戻・損益へ入らない。

`src/roi_history.py`、DBスキーマ、予測、戦略、収集、`render.yaml` は変更していない。

## 修正後の実ルート照合

本番DBはSELECT専用、HTMLキャッシュ書込みはテストハーネスで無効化して、実際のルート
`/member/strategy?from=2026-04-01&to=2026-08-16` を実行した。

| 指標 | 独立台帳集計 | ルートのtemplate context |
|---|---:|---:|
| 実運用日数 | 38日 | 38日 |
| 対象件数 | 171件 | 171件 |
| 的中 | 28件 | 28件 |
| 投資 | 17,500円 | 17,500円 |
| 払戻 | 14,350円 | 14,350円 |
| 損益 | -3,150円 | -3,150円 |
| 回収率 | 82.0% | 82.0% |

一致判定はTrue。raw 187行を無条件に合算せず、従来どおり未確定・非active・既存除外ガードを適用した台帳実績だけを合算している。

月別ルートも実行し、2026-07は実運用22日/参考9日、2026-08は実運用16日/参考0日、合計38実運用日を確認した。

## 後知恵バイアス防止と性能

- operational判定は従来どおり `_adopted_from_market_signals_cache=True` のみ。
- reconstructed行は別配列・参考日数のままで、実運用の投資/払戻/損益/回収率には不算入。
- `is_settled=0` はloaderにもcache revisionにも含めない。
- 通常表示は `_l4_daily_stats_cache_only` のまま。重い `_l4_daily_stats(..., force_full_scan=True)` は許可済みrecompute時だけ。
- 通常リクエストに増えた処理は、期間indexで絞る `MAX(updated_at), COUNT(*)` の軽量SELECTだけ。

## 追加テスト

- 実際のstrategy_key `a1_ace_motor_123_corr_tri` を使い、settled行だけが日別loaderへ入る回帰。
- 台帳revision変更直後に日別HTMLが旧キャッシュを返さない回帰。
- 月別HTMLも同様に旧キャッシュを返さない回帰。
- revision SQLがsettled/active条件を保つ契約。
- operational fixtureの投資/払戻/的中/日数が一致し、大きなreconstructed値を合算しない回帰。

## テスト結果

- focused: `56 passed`
- 必須全体: `pytest tests/ -q --basetemp=.pytest_tmp_roi_operational_full --ignore=tests/e2e --ignore=tests/round3_e2e`
  - `969 passed, 1 skipped`
  - 既存 `.pytest_cache` ACL由来の警告1件のみ
- `python -m py_compile src/web/app.py src/roi_history.py`: pass
- scoped `git diff --check`: pass

## 変更禁止事項の確認

- push/deployなし
- 本番DB書込みなし、スキーマ変更なし
- ローカル本番スケジューラ/writer起動なし
- ROI定義・戦略ロジック・予測・収集・`render.yaml`変更なし
- 数字のでっち上げなし。上記数値は本番台帳SELECTと実ルートcontextの照合値

## コミット

ローカルコミットIDは作業完了時に確定し、最終報告に記載する。
