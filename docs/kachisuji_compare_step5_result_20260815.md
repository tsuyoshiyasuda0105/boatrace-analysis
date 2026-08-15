# 勝ち筋サーチ Step 5 実装結果

実施日: 2026-08-15

## 実装概要

- `asof_race_features` を schema version 3 にし、各艇の満年齢、全国2連対率、当地2連対率を追加した。
- 既存テーブルは不足列だけを `ALTER TABLE ... ADD COLUMN` で追加する。既存の schema v2 行は更新せず、新列は NULL、以後の新規行だけが v3 になる。
- 検索条件にレース番号範囲、艇別の年齢・全国2連対率・当地2連対率、艇間比較を追加した。
- 検索画面に同じ条件と、追加・削除可能な艇間比較UIを追加した。
- ASCIIだけで記述したダブルクリック用ランチャーを追加した。既存 `/healthz` が応答する場合は二重起動しない。

## 変更ファイル

- `src/features/asof_builder.py`
- `src/search/roi_search.py`
- `src/search/strategies.py`
- `src/kachisuji_web/templates/search.html`
- `src/kachisuji_web/static/kachisuji.css`
- `tests/test_asof_builder.py`
- `tests/test_roi_search.py`
- `tests/test_strategies.py`
- `tests/test_kachisuji_web.py`
- `scripts/start_kachisuji.bat`（新規）
- `docs/kachisuji_compare_step5_result_20260815.md`（新規）
- `docs/handoff.md`（リポジトリ運用ルールに基づく作業記録）

`src/kachisuji_web/app.py` は既存の条件JSON受け渡しで対応できたため変更していない。`src/web/`、`data/boatrace.db`、`data/kachisuji_search.db` は変更していない。

## 生年月日データの調査結果

`data/boatrace.db` を SQLite URI の `mode=ro` で調査した。

- `racers.birth_date` が存在する。
- `racers` は1,643行で、`birth_date` は1,643行すべて非NULLだった。
- 全1,643値を `date.fromisoformat` で検査し、不正形式は0件だった。値域は `1949-04-26` から `2009-09-24`。
- `race_entries` 3,153,894行のうち、現在の `racers` と選手番号で結合できる行は2,827,277行だった。結合できない過去行は、仕様どおり年齢をNULLにする。
- 結合できた2,827,277行のうち、生年月日とレース日から計算した満年齢が番組表の `race_entries.age` と一致したのは2,809,353行だった。実装は仕様に従い、番組表年齢の転記ではなく `racers.birth_date` と `races.race_date` から満年齢を計算する。

## 全国・当地2連対率の調査結果

`race_entries` に次の番組表掲載列が存在した。

- 全国2連対率: `national_top_2_percent`
- 当地2連対率: `local_top_2_percent`

両列とも全3,153,894行で非NULL、値域は0.0〜100.0だった。このため、`bN_national_rate2` と `bN_local_rate2` を両方追加した。

## schema v2/v3 共存確認

- 現在の `data/kachisuji_search.db` は557,425行すべて schema v2で、Step 5の18列は未追加だった。
- 実DBのCREATE TABLE文と実在するv2行1件をメモリDBへ複製して追加マイグレーションを実行した。v2行は保持され、18列はすべて追加され、値はすべてNULLだった。元DBは読み取り専用で開き、変更していない。
- 条件コンパイラと保存手法の照合は schema v2/v3 の両方を対象にする。新条件を参照したv2行は、新列がNULLなので `condition_null` として除外される。

## compare 規約

比較条件は次の符号規約で固定した。

- `op="ge"`: `value(boat) - value(other) >= margin`
- `op="le"`: `value(boat) - value(other) <= -margin`
- `margin` は0以上の有限数で、SQLにはパラメータとしてバインドする。
- `metric` は固定ホワイトリストからだけ列名へ変換する。艇番は1〜6、`boat != other` を必須にする。
- どちらかの値がNULLなら `condition_null` として除外する。複数の比較行はANDになる。

例: 「4号艇の平均STが隣接艇最速より0.02以上遅い」は、平均STの定義が同じである場合、4号艇対3号艇と4号艇対5号艇の2行をいずれも `op="ge", margin=0.02` としてANDで表現する。専用の「隣接最速」構文は追加していない。

## adopted_strategies.md の表現可否

### この拡張で表現可能になった条件

- レース番号範囲: `7-12R`、`9-12R`、`10-12R`、`10R以降`。平和島後半型、丸亀後半弱4型、下関/多摩川後半壁型、江戸川後半へこみ型、後半エース決まり手型などのレース番号部分。
- 番組表の全国2連対率の上下限: A級事故率STへこみ型、事故率STへこみ・まくり型、浜名湖オリジナル展示候補などの全国2連対率部分。
- 番組表の当地2連対率の上下限: 現在の採用表に明示例はないが、条件軸として利用可能。
- 展示タイムの艇間比較: `展示T3 <= 展示T2` は `metric="ex_time", boat=3, op="le", other=2, margin=0` で表現できる。`tri134_acc2_ex3_tri` と `omura_132_weak2_ex3_tri` の展示比較部分が対象。
- モーター2連対率の艇間差: 弱4型の「2号艇/3号艇が4号艇より5pt以上優位」は2本の `motor_rate2` 比較をANDして表現できる。
- 番組表掲載平均ST、展示ST、全国勝率、当地勝率、全国2連対率、年齢の任意の2艇間比較。
- 年齢の上下限と艇間比較。`adopted_strategies.md` には現時点で年齢条件の明示例はない。

既存条件と組み合わせることで、少なくとも `tri134_acc2_ex3_tri` と `omura_132_weak2_ex3_tri` は記載された条件軸を検索JSONで組める。弱4型は相対モーター条件と後半R条件を組めるようになったが、下記のT-5オッズや一般戦条件が残るため手法全体は未表現である。

### まだ正確に表現できない条件

- 過去180日実測平均ST、必要出走数30走、事故率算出の出走数8走。
- STブレ（標準偏差等）、2コース勝率、コース別勝率・連対率、差し率。
- 「180日平均ST」を使う隣接艇最速との差。比較構文自体は表現できるが、現在の `avg_st` は番組表掲載値なので同じ定義ではない。
- T-5オッズ帯、推奨単勝オッズ、オッズ欠損時の厳密な除外。
- オリジナル展示の直線タイム・周回タイムと各順位、展示進入コース。
- コース別必要足、展示足適合、展示順位ギャップなどの派生スコア。
- 壁強/壁弱score、1号艇軸力、3号艇/4号艇の攻め材料、L4展開予測や `post_head_prob_up` などの複合判定。
- 攻め決まり手のコース別1着回数、まくり＋まくり差し合算回数・率。現行の決まり手条件は別定義のため代用しない。
- 一般戦、G2/G3などのグレード条件（列はあるが検索条件キーがない）。
- 複数会場の包含/除外、毎年の特定月、複数買い目のOR、条件グループのOR。別々の保存手法に分割すれば一部運用できるが、1条件JSONでは表現できない。
- 「展示タイム上位」のように順位閾値が明記されない曖昧条件。展示順位そのものは既存条件で指定可能。

## テスト・検証結果

- 指定4ファイル: 84 passed
- リポジトリ全テスト: 793 passed
- Python `py_compile`: 合格
- テンプレート内JavaScript構文検査: 合格
- `git diff --check`: 合格
- ランチャーASCII検査、healthz二重起動防止、起動コマンド、終了コメント: 合格
- 実サーバー、スケジューラ、ブラウザ、ネットワーク、production writerは起動していない。
- `data/boatrace.db` と `data/kachisuji_search.db` は読み取り専用調査のみ。push、deployは実施していない。

pytestには既存の `.pytest_cache` 作成警告が1件出たが、テスト失敗はない。

## コミット

指定メッセージのローカルコミット1つにまとめる。コミットIDは納品メッセージで報告する。pushはしない。
