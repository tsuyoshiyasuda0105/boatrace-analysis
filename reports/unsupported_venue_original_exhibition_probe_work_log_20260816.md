# 未対応会場 独自展示調査 作業ログ

作業日: 2026-08-16  
対象: 未対応の有効13会場 `[2,3,4,7,8,9,12,14,15,16,20,21,23]`  
状態: ライブ調査・限定実装・全体回帰完了。成果コミットIDは実行結果として報告。pushなし。

## 調査条件とBAN防止

- 全取得は `src.collectors._http.fetch_html()` を使用し、`config.USER_AGENT` を変更していない。
- `config.REQUEST_INTERVAL_SECONDS == 2.0` をassertし、全リクエストを単一プロセスで逐次実行した。並列取得なし。
- 調査プロセス内だけ `LAYER3_MAX_RETRIES=1`、`BOATRACE_SHARED_RATE_LIMIT=0` とし、外部DBへ接続せずプロセス内リミッタを使用した。
- 1会場最大3本。404、明示的エラーページ、実データなしで判定できた会場はそこで打ち切った。児島(16)は指定どおり1本だけ。
- 最初のサンドボックス内試行は戸田へのソケット接続前に `WinError 10013` で拒否され、会場へ到達していない。承認済み外部通信で固定URLリストだけを再実行した。
- ROI、予測、DBスキーマ、`render.yaml`、既存11会場の設定は変更していない。バックフィル、scheduler、production writer、serverは実行していない。

## 13会場の判定

「提供項目」はライブ応答からパースできた項目のみを記載した。「要追加対応」は、公式ページ内に独自展示取得機構があるものの3本以内でレース実値まで到達できず、指示書の深追い禁止により今回は追加していない。

| 会場 | リクエスト | URL / HTTP status | 応答で確認した項目 | 判定 |
|---|---:|---|---|---|
| 2 戸田 | 3 | `https://www.boatrace-toda.jp/?day=20260816&race=12` → 200（同URLをHTML/リンク確認で2回）、`https://www.boatrace-toda.jp/assets/js/race_table_original.js` → 200 | HTML本体はlap/turn/straight 0件・パース0行。公式JSに `getXmlRaceTableOriginal()` と `/race_table_original_*.xml` 取得処理あり | **公表機構あり・要追加対応**。3本以内で実値XML名を確定できず今回は追加しない。標準 `modules/yosou/cyokuzen.php` 404だけを根拠に「非公表」とするのは誤り |
| 3 江戸川 | 3 | `.../mobile/yosou/syussou.php?day=20260815&race=12` → 200（2回）、ページ内の公式リンク `.../mobile/yosou/cyokuzen.php?day=20260815&race=12` → 200 | 予想文中の「直線」1語だけ。独自計測ラベルなし、パース0行 | **非対応確定**。再取得対象外の既存設定を維持 |
| 4 平和島 | 3 | `https://www1.heiwajima.gr.jp/asp/heiwajima/kyogi/kyogihtml/index.htm` → 200（2回）、ページ内公式リンク `https://www.heiwajima.gr.jp/asp/kyogi/04/pc/yoso0212.htm` → 200 | 独自計測ラベルなし、パース0行 | **非対応確定**。深追いせず再取得対象外 |
| 7 蒲郡 | 2 | `.../recomend/recomend202608160712.htm` → 200だが当該日の実値なし。公式アーカイブ `.../recomend/recomend202605310702.htm` → 200 | **lap / turn / straight、6艇**。1号艇例 `36.59 / 4.77 / 6.27` | **提供あり・追加** |
| 8 常滑 | 1 | `https://www.boatrace-tokoname.jp/sp/raceguide/kyogi19/12/` → 404 | なし | 指示書の404打ち切りルールにより **非対応確定**。追加取得なし |
| 9 津 | 2 | `.../modules/raceinfo/?page=index_racejoho&target_day=20260816&rno=12` → 200、`.../modules/yosou/cyokuzen.php?day=20260816&race=12` → 200 | 前者はレース展望、後者は通常の直前情報。独自計測ラベルなし、パース0行 | **非対応確定**。再取得対象外の既存設定を維持 |
| 12 住之江 | 2 | `https://www.boatrace-suminoe.jp/asp/kyogi/12/pc/st0212.htm` → 200（実値なし）、`.../st0204.htm` → 200 | **lap / turn、6艇**。straightは公表なし。1号艇例 `37.73 / 11.65` | **提供あり・追加** |
| 14 鳴門 | 3 | `https://www.n14.jp/sp/index.php?day=20260816&page=yosou-cyokuzen&race=12` → 200（2回）、同URL `&run=0` → 200 | 直前情報テンプレートのみ。独自計測ラベルなし、パース0行 | **非対応確定**。深追いせず再取得対象外 |
| 15 丸亀 | 2 | `https://www.marugameboat.jp/asp/kyogi/15/pc/yoso0512.htm` → 200、`.../yoso0510.htm` → 200 | いずれも実データなし、独自計測ラベルなし、パース0行 | 指示書の空応答打ち切りルールにより **非対応確定** |
| 16 児島 | 1 | `https://hj.kojima-yosou.com/hjpc/index/20260813/12` → connection refused (`WinError 10061`) | なし | 指定どおり **1本で非対応確定**。再取得対象外の既存設定を維持 |
| 20 若松 | 1 | `https://info.wmb.jp/pc/race.php?day=20260812&race=12` → 404（`https://www.wmb.jp/error.php`へ遷移） | なし | 指示書の404打ち切りルールにより **非対応確定** |
| 21 芦屋 | 3 | `https://www.boatrace-ashiya.com/sp/index.php?day=20260815&page=yosou-cyokuzen&race=12` → 200 error page、dayなし → 200 error page、`&run=0` → 200 error page | なし | **非対応確定**。3本で打ち切り、再取得対象外 |
| 23 唐津 | 3 | `https://www.boatrace-karatsu.jp/sp/index.php?day=20260814&page=yosou-cyokuzen&race=12` → 200（実値確認と構造確認で2回）、dayなしのrace=1 → 200非開催ページ | **lap / turn / straight、6艇**。1号艇例 `36.37 / 5.19 / 7.89` | **提供あり・追加**。複数段ヘッダー対応を最小追加 |

合計29リクエスト。会場別上限は全て遵守し、児島は1本。実行時間も既存リミッタにより最低2秒間隔で逐次化した。

## 戸田の結論

戸田は `modules/yosou/cyokuzen.php` が404でも、公式トップページが読み込む `race_table_original.js` に独自展示XML取得処理が存在する。したがって結論は「元々非公表」ではなく、**公表機構はあるが、現行コレクタが必要とするレース別XML URLと実値を3リクエスト以内に確定できなかったため要追加対応**である。

指示書の「構造が大きく違い簡単に解釈できない会場は要追加対応として記録のみ」に従い、推測したXML名を追加で叩かず、`SOURCE_PATTERNS` / `VENUE_FIELD_CAPABILITIES` には加えていない。通常収集・欠損検索・バックフィルの対象にもならないため、無駄な再取得は発生しない。

## 実装

- `SOURCE_PATTERNS` に蒲郡(7)、住之江(12)、唐津(23)の実取得URLだけを追加。
- `VENUE_FIELD_CAPABILITIES` は実際に取れた項目だけを追加。
  - 7: `lap, turn, straight`
  - 12: `lap, turn`
  - 23: `lap, turn, straight`
- 唐津の2段ヘッダー（rowspan/colspan）を列位置へ展開する小さな共通処理をパーサへ追加。単段ヘッダーおよび既存の特殊パーサ経路はそのまま。
- 非対応確定会場と要追加対応の戸田は `SOURCE_PATTERNS=[]` のままで、`supported_stadiums()`、通常収集、欠損検索、再取得CLIから除外される。

## テスト

- focused: `38 passed`（collector/parser/fixture/cron missing detection/backfill guard）
- 追加テスト:
  - 新3会場のURLテンプレートと能力マップ
  - 唐津の複数段ヘッダーで6艇×3項目を正しく解析
  - 住之江はstraightなしでも完了、蒲郡はstraight欠損なら未完了
  - 未設定会場が再取得対象にならない
  - 既存11会場の能力とソースが不変
- Python compile: pass
- `git diff --check`: pass
- 指定全体回帰:
  - `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/e2e --ignore=tests/round3_e2e`
  - `964 passed, 1 skipped, 1 warning in 19.85s`
  - warningは既存 `.pytest_cache` のWindows ACL warningで、製品テスト失敗なし
- ローカルDBはURI `mode=ro` + `PRAGMA query_only=ON` でのみ確認し、`boatrace.db` の `race_original_exhibitions=112,572`、`kachisuji_search.db` の `accident_events=49,189`。本作業によるDB書き込みなし。

## 運用状態

- push / deploy / production writer / scheduler / backfill: 未実行
- ローカルserver / browser / watcher / background helper: なし
- 削除・rollback: なし
