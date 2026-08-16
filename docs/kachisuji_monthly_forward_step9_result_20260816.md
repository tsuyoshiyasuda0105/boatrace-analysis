# 勝ち筋サーチ Step 9 実装結果

実施日: 2026-08-16

## 実装結果

- `search_roi` の返却値に、データが存在する年月だけを昇順で収録する `monthly` を追加した。
- 年別と月別は、検索結果行を追加クエリなしで同じ1パスにより集計する。既存 `yearly` のキー、並び順、丸め規則は維持した。
- 年別表の各年行にネイティブ `button` の「＋」を追加した。`aria-expanded` と `aria-controls` を持ち、クリックまたはキーボードで年ごとの月行を独立して開閉できる。
- マイ手法に探索時・全期間・フォワードの3成績、運用判定、判定までの残り件数、フォワード累積損益スパークラインを追加した。
- 新API `GET /api/strategies/<id>/performance` と `GET /api/strategies/performance` を追加した。既存APIのパスと応答は変更していない。

## forward 起点の規約

`created_at` を `Asia/Tokyo` に変換してJSTの保存日を決め、`date_from` をその翌日に設定する。したがって、保存当日のレースは、保存時点で結果が判明していた可能性を安全側に扱って必ず除外する。タイムゾーンなしの旧日時はUTCとして解釈する。

この境界規約は `src/search/strategies.py` の `_strategy_performance` docstringにも明記した。合成DBでは、保存日前・保存当日・翌日・翌々日のレースを配置し、保存当日が除外され翌日以降だけが `forward` に入ることを検証した。

`overall` は保存条件をコピーして `date_from` / `date_to` を削除してから、`forward` はさらに上記の翌日を `date_from` に設定してから、それぞれ既存 `search_roi(..., fast=True)` を呼ぶ。ROI、的中、欠損除外、CIのロジックは再実装していない。

## 一覧の計算方式

画面は手法数増加時の待ち時間を抑えるため、初期一覧では既存 `GET /api/strategies` だけを自動取得し、各カードの「成績を見る」ボタンで単一手法APIを取得する方式を採用した。取得後に3成績・判定・スパークラインをカード内へ表示する。

一括API `GET /api/strategies/performance` は全有効手法の compact な3成績と判定を返すが、レスポンス量を抑えるため `forward_curve` を省略する。曲線は単一手法APIだけに含める。

## 判定と累積損益

- `n < 30`: `pending` / 判定待ち / 残り `30 - n` 件
- `n >= 30` かつ `roi >= 130`: `promote` / 昇格候補
- `n >= 30` かつ `70 <= roi < 130`: `watch` / 監視中
- `n >= 30` かつ `roi < 70`: `demote` / 降格候補

フォワード損益は1レース100円固定で `払戻 - 100` を計算し、同日分を合算して日付順に累積する。200日以下は全点を返し、201日以上は等間隔で最大200点へ間引く。最初と最後の点は必ず残す。画面はゼロ線を破線、終点を丸で強調し、最終損益が0以上なら良色、未満なら悪色を使う。

## 性能実測

`data/kachisuji_search.db` をSQLite URI `mode=ro` で読み、代表的な条件5件を一時strategy DBへ既存保存経路で登録して Flask `test_client` から計測した。実測時の検索DBは 1,651,187,712 bytes、mtime は計測前後で不変だった。

| 操作 | 実測 |
|---|---:|
| 5件の初期一覧 `GET /api/strategies` | 0.0023秒 |
| 5件の一括成績 `GET /api/strategies/performance` | 3.0828秒 |
| 単一手法・曲線付き `GET /api/strategies/1/performance` | 0.3364秒 |

一括成績は1手法につき `overall` と `forward` の2回 `search_roi` を実行する。画面でボタン方式を採用したため、初期一覧ではこの再計算時間を負担しない。

## 変更ファイル

- `src/search/roi_search.py`
- `src/search/strategies.py`
- `src/kachisuji_web/app.py`
- `src/kachisuji_web/templates/search.html`
- `src/kachisuji_web/static/kachisuji.css`
- `tests/test_roi_search.py`
- `tests/test_strategies.py`
- `tests/test_kachisuji_web.py`
- `tests/e2e/test_kachisuji_e2e.py`
- `docs/kachisuji_monthly_forward_step9_result_20260816.md`

## テスト結果

- focused synthetic unit/API: 112 passed
- Kachisuji unit/contract: 159 passed（既存149 + Step 9新規10）
- 通常E2E: 55 passed（既存53 + Step 9新規2）
- DoD対象合計: **214 passed（既存202 + Step 9新規12）**
- E2E前後のport 8090 listener: 0
- Python compilation: pass
- scoped `git diff --check`: pass

pytest は既存 `.pytest_cache` のWindows ACLに関するwarningを1件出したが、テスト結果と専用basetempには影響しなかった。

## データ安全性と既知の制限

- `data/boatrace.db` には接続していない。
- `data/kachisuji_search.db` は既存 `search_roi` の読み取り専用URI接続だけで使用し、書き込み、スキーマ変更、新規テーブル作成をしていない。
- 実DBの `strategies` テーブルへ列を追加していない。実成績はAPI呼び出し時に都度計算する。
- 保存直後など翌日以降の収録レースがない場合、`forward` は `n=0`, `roi=0.0`, `pending`, 残り30件、空の曲線を正常に返す。
- 既存の保存済み `final` オッズ条件はStep 8の方針どおり自動変換しないため、性能再計算でもT-5限定の日本語エラーになる。
- サーバー、Chromium、スケジューラ、writer、watcherは終了済みで、pushは行っていない。
