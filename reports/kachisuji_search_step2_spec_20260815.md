# 勝ち筋サーチ Step 2 実装仕様書 — 条件検索×回収率エンジン

作成: 2026-08-15 リン（Claude Code）/ 発注先: Codex
前提: Step 1（コミット f2ee23a）で `data/kachisuji_search.db` の `asof_race_features`
テーブル（1レース=1行、schema_version=2）が完成済み。スキーマは
`src/features/asof_builder.py` と `reports/kachisuji_asof_step1_spec_v2_20260815.md` を参照。

## 目的

条件オブジェクト（デモUIの検索条件に対応）を受け取り、asof_race_features を検索して
回収率・的中率・母数・信頼区間・年別内訳を返す**読み取り専用の検索エンジン**を実装する。

## 絶対的な制約（違反禁止）

1. **新規ファイルのみ作成。既存ファイルの変更は一切禁止**。
2. `data/kachisuji_search.db` は**読み取り専用**（書込み・DDL禁止）。`data/boatrace.db` には接続もしない。
3. **開発・テストは合成フィクスチャDBのみで行う。実物の `data/kachisuji_search.db` に対する実行は禁止**
   （現在バックフィル実行中のため。実データでの動作確認は発注者=リンが後で行う）。
4. ネットワーク・スケジューラ・デプロイ・push 禁止。Web エンドポイント追加も今回は範囲外。
5. コミットは main へのローカルコミット1つ。メッセージ: `Add condition search ROI engine (kachisuji step 2)`。

## 作成するファイル

- `src/search/__init__.py`（空でよい）
- `src/search/roi_search.py` — エンジン本体
- `scripts/search_roi.py` — CLI（条件JSONファイル or 標準入力 → 結果JSON出力）
- `tests/test_roi_search.py` — テスト
- `docs/kachisuji_search_step2_result_20260815.md` — 結果レポート

## 条件オブジェクト仕様（JSON）

全キー省略可。省略・null は「指定なし」。

```json
{
  "venue": 12,                       // jcd 1-24
  "bet": {"type": "sanrentan", "first": 1, "second": 2, "third": 3},
                                     // type: "tansho" | "nirentan" | "sanrentan"
                                     // tansho は first のみ / nirentan は first,second
  "weather": ["晴", "曇"],           // OR 条件
  "wind_speed": {"max": 1.0},        // {"min":x} {"max":x} 併用可
  "tide_phase": "満潮前後",
  "female_present": 0,               // 0=男性のみ 1=女性あり
  "class_mix": "A1単騎",
  "day_index": "初日",
  "daypart": "ナイター",
  "date_from": "2023-05-01", "date_to": "2024-12-31",
  "boats": {
    "1": {
      "class": ["A1", "A2"],         // OR 条件
      "racer_id": 4320,
      "avg_st": {"max": 0.15},
      "national_rate": {"min": 6.0},
      "local_rate": {"min": 6.0},
      "motor_rate2": {"min": 40},
      "ex_rank": {"min": 1, "max": 3},
      "ex_dev": {"faster_by": 0.10},  // 平均より0.10秒以上速い。"slower_by" も可
      "ex_st": {"max": 0.10},
      "kimarite": {"name": "nige", "rate_min": 60},
      "accident_rate": {"min": 0.5}   // {"max":x} も可
    },
    "2": { ... }                      // "1"〜"6"
  }
}
```

## 検索セマンティクス（重要）

1. **NULL は「判定不能」**: ある条件が参照する列が NULL の行は、条件不成立ではなく
   **母数から除外**する（excluded にカウント）。例: 展示条件を使う検索では展示未収集レースは
   分母にも分子にも入れない。条件が参照しない列の NULL は無関係。
2. 買い目の判定: bet.type に応じて result_tansho / result_nirentan / result_sanrentan と
   payout_* を使う。的中 = 結果が指定組と完全一致。結果列 NULL の行は除外扱い。
3. 回収率 = Σ(的中行の payout) ÷ (N × 100) × 100（%表記。1点100円買い想定。payout は100円あたり払戻金）。
   ※payout 列の単位（100円あたりか）は Step 1 実装を確認して合わせ、docstring に明記。
4. 信頼区間: 回収率の 95%CI。デフォルトは払戻分布に基づくブートストラップ（1000 iter、
   乱数 seed は引数で固定可能・デフォルト seed=42 で決定的に）。`--fast` で正規近似。
5. 返却JSON:
```json
{
  "n": 412, "hits": 47, "hit_rate": 11.4, "roi": 108.2,
  "roi_ci_low": 71.0, "roi_ci_high": 149.3,
  "excluded": {"result_missing": 3, "condition_null": 120},
  "yearly": [{"year": 2023, "n": 120, "hits": 13, "roi": 95.1}, ...],
  "warnings": ["n<100: 上振れの可能性"],
  "effective_date_range": ["2023-05-01", "2026-08-14"]
}
```
6. **小N警告**: n<30 → "n<30: 偶然の可能性が高い" / n<100 → "n<100: 上振れの可能性" を warnings に。
7. 決まり手・事故率条件が有効な場合、`effective_date_range` の開始を 2023-05-01 より前にしない
   （それ以前は集計助走期間のため NULL であり、1の規則で自然に除外されるはずだが、明示もする）。

## 性能要件

- 単一 SELECT（結合なし）で WHERE を構成し、Python 側でブートストラップ。
- 55万行に対して `--fast` で 2秒以内、ブートストラップ込みで 10秒以内を目安に設計
  （実測確認はリンが後で行うので、フィクスチャでのロジック検証を優先してよい）。
- 接続は `sqlite3.connect(path, uri=True)` の読み取り専用モード（`mode=ro`）で開く。

## CLI 仕様

```
python scripts/search_roi.py --conditions cond.json          # ブートストラップCI
python scripts/search_roi.py --conditions cond.json --fast   # 正規近似
echo '{...}' | python scripts/search_roi.py --stdin
python scripts/search_roi.py --db data/kachisuji_search.db --conditions cond.json  # DB明示
```
出力は UTF-8 の JSON 1個（人間向け整形は `--pretty`）。

## テスト仕様（合成フィクスチャDBで）

1. 単勝/2連単/3連単それぞれの的中判定と回収率計算が手計算と一致
2. NULL除外規則: 展示条件あり検索で展示NULL行が excluded.condition_null に入り、分母に入らない
3. 展示条件なし検索では同じ行が普通に母数に入る（無関係NULLは除外しない）
4. boats 条件（class OR / racer_id / min-max / faster_by）の各演算子
5. 小N警告の閾値
6. ブートストラップCI が seed 固定で決定的、かつ点推定を挟む
7. 年別内訳の合計が全体と一致
8. 空結果（n=0）で例外にならず n:0 を返す

実行: `.venv/Scripts/python.exe -m pytest tests/test_roi_search.py -q`

## 完了条件（DoD）

1. テスト全件グリーン（既存テストにも影響なし = 新規ファイルのみなので自明）
2. `docs/kachisuji_search_step2_result_20260815.md` に: 作成ファイル / テスト結果 /
   条件→SQL変換の設計概要 / payout 単位の確認結果 / 既知の制限
3. ローカルコミット1つ（push しない）

## 実装上の注意

- 列名・値の実際の表現（weather の文字列、kimarite 列名等）は `src/features/asof_builder.py` を
  読んで正確に合わせる。推測禁止。
- SQL は必ずパラメータバインド（文字列連結禁止）。条件キーのホワイトリスト検証を行い、
  未知キーはエラーにする（将来 Web 公開時の安全性の土台）。
