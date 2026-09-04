# 作業指示書: パーサー fixture テスト整備 (Codex CLI 用)

作成: 2026-08-15 / 発注者: リッキー / 検品: リン (Claude)
リポジトリ: `C:\boat_project\boatrace-analysis` (正本のみ。他の場所に checkout を作らない)
背景: HP 構造が変わるとパーサーが**無音で全滅**する弱点 (TODO #12)。実データ由来の
fixture を固定し、壊れたら CI/テストで**大声で落ちる**ようにする。

## 絶対ルール (毎回同じ)

1. **origin/main への push 禁止**。コミットはローカル main まで。
2. **パーサー本体のロジックを変更しない** (これはテスト整備タスク)。追加するのは
   `tests/` 配下のテストと `tests/fixtures/` 配下の固定データのみ。
3. もしテスト作成中にパーサーの**実バグ**を見つけたら、**直さずに作業ログへ記録**して
   報告する (別タスクで扱う)。勝手にロジックを直さない。
4. ROI 戦略・予測・DB スキーマ・render.yaml・app.py は触らない。
5. テスト: `.venv/Scripts/python.exe -m pytest tests/ -q` — 現行 main は **658 passed**。
   1件でも壊したら自分の変更を疑うこと。
6. 作業ログ `reports/parser_fixture_tests_work_log_20260815.md` に、対象パーサー・
   採用した fixture の出所・アサート方針・見つけた懸念を記録。
7. コミットはパーサー単位でまとめてよい (2〜4コミット目安)。

## 対象パーサー (専用テストが無い/薄いもの優先)

`src/parsers/` 配下、`__init__.py` を除く7本。**専用テストが既にあるのは
`official_b.py` (`tests/test_official_b_parser.py`) だけ。** 残り6本を優先:

| パーサー | 役割 | 実サンプルの在り処 (例) |
|---|---|---|
| `official_k.py` | Layer1 競走成績 (cp932 固定幅) | `data/raw/results/K*.TXT` |
| `official_f.py` | ファン手帳 (性別/級別) | `data/raw/fan/` (LZH 解凍後の固定幅 TXT) |
| `beforeinfo.py` | Layer3 直前情報 (展示T/ST/部品交換) | `data/raw/beforeinfo/<日付>/*.html` |
| `odds.py` | Layer3 三連単オッズ HTML | `data/raw/odds3t/` (無ければ beforeinfo と同様の owpc HTML を探す) |
| `original_exhibition.py` | 独自展示 | `data/raw/original_exhibition/` |
| `result_html.py` | 結果速報 HTML パース | 速報 HTML サンプル (無ければ小さく自作) |

`official_b.py` は既存テストがあるので**任意** (fixture 化して補強するのは歓迎、ただし
既存テストを壊さない)。

## やること

1. 各パーサーの**トップレベルのパース関数**を特定する (ファイルを読んで、外部から
   呼ばれる公開関数。例: `parse_*`, `extract_*`)。
2. `data/raw/` から**実在の小さいサンプル1〜2件**を選び、
   `tests/fixtures/parsers/<parser名>/` にコピーする (サイズが大きい HTML は
   **対象1レース分など最小限に切り詰めてよい**が、パーサーが読む構造は保つ)。
   - cp932 の固定幅 TXT (K/F/B) は**エンコーディングを保持**してコピーすること
     (バイト列を変えない。UTF-8 に変換しない)。
3. `tests/test_<parser名>_fixture.py` を作り、fixture を読ませて**安定するキー項目**を
   golden 値としてアサートする:
   - 良いアサート例: 「パースできたレコード件数」「先頭レコードの選手番号/会場/
     数値フィールドの厳密値」「必須キーが全部存在する」。
   - 避けるアサート: 実行日時・当日限定で変わる値・順序が不安定なもの。
4. **異常系も1つ**入れる: 空入力/欠損行/想定外フォーマットで、パーサーが
   例外で死なず「空リストや None を穏当に返す」ことを確認 (現状の挙動に合わせる。
   挙動を変えるのが目的ではない)。
5. fixture の出所 (元ファイル名・日付・会場・切り詰めたかどうか) を各テスト冒頭の
   docstring かコメントに書く。

## 受け入れ条件

- [ ] `official_k` / `official_f` / `beforeinfo` / `odds` / `original_exhibition` /
      `result_html` の6本に、実 fixture 由来の golden テストが最低1本ずつ付いた
- [ ] 各パーサーに異常系テストが最低1本 (死なないことの確認)
- [ ] fixture は `tests/fixtures/parsers/` 配下に配置、cp932 ファイルはバイト保持
- [ ] `pytest tests/ -q` 全件 green (既存 658 を割らない)
- [ ] パーサー本体は無改変 / push していない / 作業ログ提出
- [ ] (もしパーサーの実バグを発見したら) 作業ログに再現手順付きで列挙

## 検品 (リンが実施)

完了報告後、リンが「fixture が実データ由来か」「アサートが構造変化を捕まえる強さか」
「パーサー本体が無改変か」「全テスト green か」を照合する。指示書との乖離があれば差し戻す。
