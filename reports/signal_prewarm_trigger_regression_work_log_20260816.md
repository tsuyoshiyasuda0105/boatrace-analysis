# シグナル prewarm trigger 回帰修正 作業ログ

- 作業日: 2026-08-16
- 対象: `scripts/prewarm_strategy_pages.py`
- 方針: 指示書の推奨方針A
- push / deploy / production write: 未実施

## 根本原因

exhibition cron は `BOATRACE_TASK_TRIGGER=render-exhibition-detail-refresh` を設定し、その子プロセスとして `prewarm_strategy_pages.py --mode signals` を実行する。prewarm側は `os.environ.setdefault("BOATRACE_TASK_TRIGGER", "render-prewarm")` だったため、親から継承した値を上書きしなかった。

market-signalsの再計算ガードは許可済みtriggerだけを認めるが、継承値は許可集合に含まれない。このため正当なprewarmでも重い計算に入れず、`X-Boatrace-Cache: recomputed`検証が失敗し、exhibition cronが誤ってfailureになっていた。

修正前の読み取り再現では、継承値を設定してprewarmスクリプトをロードした後もtriggerが`render-exhibition-detail-refresh`のままだった。

## 修正

prewarmスクリプトの起動時に、Webアプリをimportする前の既存位置で次を明示代入するよう変更した。

```python
os.environ["BOATRACE_TASK_TRIGGER"] = "render-prewarm"
```

これにより親cronの種類に関係なくprewarmプロセス内だけが既存の許可済みtriggerを使う。`EXPENSIVE_RECOMPUTE_TRIGGERS`、market-signalsの計算処理・数値、ROI、予測、DBスキーマ、`render.yaml`、収集処理は変更していない。

## 回帰テスト

追加した確認:

1. exhibition cronから継承したtriggerをprewarmが`render-prewarm`へ上書きする。
2. `render-exhibition-detail-refresh`自体はmarket-signals APIを直接再計算許可しない。
3. 既存のtrigger無し人間リクエストは、`recompute=1`やプロセス全体overrideがあっても重いDB経路へ入らずpendingを返す。
4. 許可済み`render-prewarm`経路は既存の重い再計算経路へ入る。
5. シグナルprewarm成功時、exhibition cronの最終判定はsuccess・終了コード0になる。
6. prewarmは引き続き`X-Boatrace-Cache: recomputed`を必須として検証する。

結果:

- 対象テスト: 78 passed
- 指定全件: 1013 passed, 1 skipped
  - 実行条件: `pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e --basetemp=.pytest_tmp_signal_prewarm_full`
  - 初回は共有Windows一時領域の既存ACLエラーでfixture setupが失敗したため、記録済みの専用basetempで同条件を再実行した。

## 整合性・運用確認

- 変更ファイル監査でROI、予測、DBスキーマ、`render.yaml`、収集、data配下の差分なし。
- ローカル`data/boatrace.db`をURI `mode=ro`かつ`PRAGMA query_only=ON`で開き、結果・事故・ROI関連テーブルを読み取り確認した。
  - `race_results`: 3,270,776
  - `racer_accident_events`: 41,984
  - `racer_accident_period_stats`: 33,187
  - `racer_accident_rank_snapshots`: 1,622
  - `roi_race_history`: 5
- DBへの書き込み、scheduler起動、サーバー起動、ブラウザ起動なし。
- 専用pytest basetempは削除済み。タイムアウトした読み取り専用DB診断の子Pythonも停止済み。
- market_signals数値ロジックは無変更。人間リクエストのブロックを維持したまま、正当なprewarmだけが再計算可能になった。
- push、deployは行っていない。
