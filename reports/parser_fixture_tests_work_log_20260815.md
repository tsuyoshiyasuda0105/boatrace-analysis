# パーサー fixture テスト作業ログ

作業日: 2026-08-15

対象リポジトリ: `C:\boat_project\boatrace-analysis`

基点: `1d69d86` (`origin/main`)

方針: パーサー本体を変更せず、実データfixtureとgolden/異常系テストのみを追加。pushなし。

## 対象・fixture出所・アサート方針

### `official_k`

- 公開関数: `parse_k_text(text, target_date)`
- fixture: `tests/fixtures/parsers/official_k/K240105_omura_01.TXT`
- 出所: 公式配布 `https://www1.mbrace.or.jp/od2/K/202401/k240105.lzh` 内の `K240105.TXT`。2024-01-05 大村1Rについて、会場ヘッダーと1Rブロックだけを抽出。
- バイト保持: 選択した各行はcp932の元バイトを変更せず保持。fixture SHA-256は `18714a334ce85841a8a7998903d16064a6a4a69fdd6194e1fea6ec7889226ec5`。
- golden: 1レース、race ID/会場/レース番号、風・波・天候、決まり手、6着分、先頭選手3773、ST/タイム、主要払戻。
- 異常系: 空文字が `[]` を返す。
- コミット: `93591be`（テスト/fixture）、`b352a99`（Git改行変換を無効化して元バイトを再登録）

### `official_f`

- 公開関数: `parse_fan_file(path)`
- fixture: `tests/fixtures/parsers/official_f/fan2604_first_record.txt`
- 出所: `data/raw/fan/fan2604.txt`（公式2026年4月ファン手帳）の先頭1レコード。元CRLF込み418バイトを未変換で抽出。
- バイト保持: fixture SHA-256は `2a88fd901f31efb3e05ecea901cb98b44e357492bcdb838f5cfd9844523c7a16`。
- golden: 1件、登録番号2538、氏名/カナ、支部、級別、生年月日、性別の完全一致。
- 異常系: 空ファイルが `[]` を返す。
- コミット: `a637186`（テスト/fixture）、`37d0b84`（Git改行変換を無効化して元バイトを再登録）

### `beforeinfo`

- 公開関数: `parse_beforeinfo(html)`
- fixture: `tests/fixtures/parsers/beforeinfo/beforeinfo_20260506_01_01.html`
- 出所: `data/raw/_test/beforeinfo_20260506_01_01.html`、2026-05-06 桐生1R。1レースの完全HTMLを構造切り詰めなしで採用。
- golden: 6艇、艇番順、1号艇の展示T/チルト/進入/ST、5号艇のF.02、天候番号・風向風速・波高・気温・水温、必須キー。
- 異常系: 空文字で空のboatsと既定値/Noneを返す。
- コミット: `ede523e`

### `odds`

- 公開関数: `parse_trifecta_odds(html)`
- fixture: `tests/fixtures/parsers/odds/odds3t_20260506_01_01.html`
- 出所: `data/raw/_test/odds3t_20260506_01_01.html`、2026-05-06 桐生1R。1レースの完全HTMLを構造切り詰めなしで採用。
- golden: 三連単120通りの完全な組合せ集合と `1-2-3=11.0`、`1-2-6=131.4`、`6-5-4=2141.0`。
- 異常系: 空文字が `{}` を返す。
- コミット: `378576e`

### `original_exhibition`

- 公開関数: `parse_original_exhibition(html)`
- fixture: `tests/fixtures/parsers/original_exhibition/20260729_19_12_shimonoseki.html`
- 出所: `data/raw/original_exhibition/2026-07-29/19_12_shimonoseki_group_cyokuzen.html`、2026-07-29 下関12R。完全HTMLを構造切り詰めなしで採用。
- golden: 6艇、艇番順、全艇の周回/回り足/直線キー、1号艇と6号艇の数値、選手名raw text。
- 異常系: 空文字が `[]` を返す。
- コミット: `95c3d31`

### `result_html`

- 公開関数: `parse_result_html(html)`
- fixture: `tests/fixtures/parsers/result_html/raceresult_20260814_01_01.html`
- 出所: 2026-08-15に公式 `raceresult?rno=1&jcd=01&hd=20260814` から取得した2026-08-14 桐生1Rの完全HTML。切り詰めなし。
- golden: 6艇の着順/艇番/タイム、決まり手「まくり差し」、三連単と単勝払戻、天候6項目、必須キー。
- 異常系: 空文字が `None` を返す。
- コミット: `9ee0ec3`

## テスト結果

- 変更前: `.venv/Scripts/python.exe -m pytest tests/ -q --basetemp .pytest_tmp_parser_fixtures_20260815` → `658 passed`
- 追加fixtureテスト: 同一basetempで対象6ファイル → `12 passed`
- 変更後全件: 同一basetempで `tests/` → `670 passed`
- pytest cacheディレクトリには既存のWindows権限由来 `PytestCacheWarning` が1件出たが、収集・assertion・終了コードはすべてgreen。

## 発見した懸念（未修正）

### 直前情報の実HTMLで主要値が全欠落するケース

- 対象: `data/raw/beforeinfo/2026-06-21/24_01.html`（2026-06-21 大村1R）
- 再現:
  1. UTF-8でHTMLを読む。
  2. `src.parsers.beforeinfo.parse_beforeinfo(html)` を実行する。
  3. `boats` は艇番1〜6の6件になるが、全艇の `exhibition_time`、`course_number`、`start_timing_exhibition` が `None`。天候・風・波・気温・水温もすべて `None`。
- 比較: 採用fixture（2026-05-06 桐生1R）では同じ公開関数が展示T/ST/進入/天候を取得できる。
- 判断: 会場または取得時点のDOM差異に追随できていない実バグ候補。絶対ルールに従い、パーサー本体は変更していない。別タスクでDOM比較と対応要否を調査する。

## 絶対ルール確認

- `src/parsers/`、`app.py`、ROI、予測、DBスキーマ、`render.yaml` は無変更。
- production scheduler/Supabase writerは未実行。
- `origin/main`へのpush、deployは未実行。ローカル`main`のみ更新。
