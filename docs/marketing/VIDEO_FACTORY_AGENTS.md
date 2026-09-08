# AGENTS.md — 競艇動画ファクトリー 引き継ぎ（正本）

このファイルは「競艇｜バックテストLAB」のマーケ動画（YouTube本編・YouTubeショート・
インスタReel・CM）を作るための **AI向け作業指示書** です。
別PCの ChatGPT / Codex / Claude に動画制作を頼むときは、**まずこのファイルを読ませてください。**

最終更新: 2026-09-08

---

## ★ 最初に読むここ（START HERE）

作業場: `C:\boat_project\lecture-factory\`（＝このツール群がある場所）
Python: `C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe`（専用venvは無い。boatrace側を使う）

**いまの標準は「1題材 = 4点セット」を1コマンドで作ること。**
1題材につき **① YouTube本編（横・実演入り）＋ ② ショート（縦）＋ ③ 縦サムネ ＋ ④ 横サムネ** を揃える。

```powershell
# 題材キーを渡すだけで「作る→サムネ作る→揃ってるか確認→非公開でアップ」まで一本道
& C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe tools\make_set.py kyoteki
& ...\.venv\Scripts\python.exe tools\make_set.py --list          # 題材キー一覧
```

**絶対ルール（安全）:**
- アップロードは**常に非公開(private)**。公開ボタンはリッキーさん本人が押す。`--public` を勝手に付けない。
- **サムネが無ければアップしない**（`make_set.py` が自動で中止する）。
- APIキー・トークン（`gemini_key.txt` / `secrets/`）を**チャット・ログ・コミットに絶対出さない**。
- 数字は10年・約55万レースの実測（DB: `data/kachisuji_search.db`）に基づく。誇張しない。
- **実在選手を否定的に名指ししない**（「弱い/大崩れ」ではなく「苦戦しやすい」等の中立表現）。

**このディレクトリは git 管理外**（→ 末尾「別PCへの渡し方」を必読）。

---

## ★ 標準の作り方: 1題材 = 4点セット

### 1) 題材台帳 `tools/topics.py`
すべての起点。ここに1ブロック足すと、ショート/本編/サムネ2枚/投稿タイトルが揃う。
1件は次を持つ: `label`（人が読む名前）/ `title_long`・`title_short`（YouTubeタイトル）/
`desc`（概要欄txt）/ `short_builder`・`long_builder`（生成スクリプト名。無ければNone）/
`long_file`・`short_file`（出力先）/ `thumb`（`thumbs.make_pair` に渡す引数）。

登録済みの題材キー（`make_set.py --list` で最新を確認）:
| key | 内容 |
|-----|------|
| `kyoteki` | 強敵に強い1号艇TOP8（A1が2号艇でも崩れない） |
| `age` | 1号艇の旬は何歳か（年齢別1着率） |
| `joshi` | 女子戦は攻略できるのか |
| `tobi` | インが飛ぶ予兆を20個検証 |
| `yowai` | A1が来ると苦戦しやすい1号艇TOP8 |
| `accident` | 事故率が高い選手がいても荒れない |
| `wind` | 風が強いと1号艇は落ちる |
| `venue` | 会場で1号艇1着率が20pt違う |
| `rain` | 雨は荒れない |

### 2) 一本道ランナー `tools/make_set.py`
```powershell
python tools/make_set.py kyoteki                              # 作る→サムネ→確認→非公開アップ
python tools/make_set.py kyoteki --publish-at "2026-09-09 19:00"  # 予約公開つき(JST)
python tools/make_set.py kyoteki --skip-build                # 動画は作らずサムネ＋アップだけ
python tools/make_set.py kyoteki --no-upload                 # 作るだけ（アップしない）
python tools/make_set.py kyoteki --only short                # ショートだけ 等
```
実行前に「何が揃って何が足りないか」を必ず表示する。サムネ欠品ならアップ中止。

### 3) サムネ生成 `tools/thumbs.py`
`make_pair(key, head1, head2, badge, rows, foot, sub, big)` で **縦(1080x1920)＋横(1280x720)** を同時生成。
出力は `thumb_<key>.png`（縦）と `YT_<key>_thumb.png`（横）。題材ごとにスクリプトを書かなくてよい。

### 4) 本編・ショートの中身（ビルダー）
- 本編（横1280x720）: `build_lecture_<key>.py`。導入→比較カード→**アプリ実演の録画**→まとめ。
  実演は収録サーバー `tools/serve_backtest_for_recording.py`（port 5070）を先に起動し、
  `tools/record_lecture_<key>.py` or `tools/record_demos.py` で撮る。操作要素は**赤枠**、
  認証バッジは隠す。汎用の設定駆動ビルダー `build_lecture_simple.py`（wind/venue/rain）もある。
- ショート（縦1080x1920）: `make_short_<key>.py`。**標準レイアウトは v2＝全画面＋巨大数字**
  （見本 `make_short_tobi_v2.py`。数字フォント180〜220、カウントアップ、下帯）。
  旧v1（黒余白が多い）は使わない。

### 5) YouTube 認可とアップロード
- 認可（初回だけ人がブラウザで「許可」）: `tools/youtube_auth.py`。
  `secrets/client_secret.json` を置いて実行 → `secrets/youtube_token.json` が保存され以後自動。
  チャンネルは「競艇バックテストLAB」。Google側でテストユーザーに両メール登録済み。
- アップ: `tools/youtube_upload.py`。既定 private、`--publish-at "YYYY-MM-DD HH:MM"`(JST) で予約公開、
  `--short` でショート扱い、`--thumb` でサムネ添付、`--desc-file` で概要欄。**--public の自動経路は無い。**
- 概要欄は `scripts/yt_desc_<key>.txt`。

---

## 0. 実行環境

- Python: `C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe`
- FFmpeg (Gyan 9.0):
  `C:\Users\tsuyo\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.exe`（`ffprobe.exe` も同ディレクトリ）
- OS: Windows 11 / PowerShell。日本語出力は `$env:PYTHONIOENCODING="utf-8"`。
- ffmpeg に渡すファイル名は英数字推奨（Git Bash で日本語名は Illegal byte sequence になりがち）。

---

## 1. 音声ナレーションは Gemini TTS（2026-08-29〜）

CM・看板動画・題材動画のナレーションは **Google Gemini TTS**。普段のReel量産は edge-tts でよい。

### 確定した声（標準）
| 用途 | 声名 | 印象 |
|------|------|------|
| 男性 | **Charon** | 落ち着いた低め・王道 |
| 女性 | **Callirrhoe** | 自然体・リラックス |

他の声は `python tools/gemini_tts.py --list`（全30種、日本語可）。

### ツール `tools/gemini_tts.py`
```powershell
python tools/gemini_tts.py "台詞" out.wav Charon   # 1行生成
python tools/gemini_tts.py --list                  # 声一覧
python tools/gemini_tts.py --check                 # キー確認
```
- Python: `import gemini_tts; gemini_tts.synth(text, wav, "Charon")`
- 出力 24kHz/mono/16bit WAV（合成前に ffmpeg で `-ar 44100 -ac 1` に揃える）。
- 429（レート制限）自動リトライ内蔵。
- キーは `gemini_key.txt` 1行目 or 環境変数 `GEMINI_API_KEY`。**中身を出さない。** 課金有効。
- 読みの既知修正（ナレーションのみ。画面の漢字は変えない）: 格下=かくした / 茅原=かやはら / 毒島=ぶすじま。

---

## 2. CM のビルド `tools/build_cm.py`
```powershell
python tools/build_cm.py all --voice gemini:Charon        # Gemini男声
python tools/build_cm.py all --voice gemini:Callirrhoe    # Gemini女声
python tools/build_cm.py all --voice ja-JP-KeitaNeural    # edge-tts（プレフィックス無し）
python tools/build_cm.py all --voice gemini:Charon --no-bgm
```
- 出力: `out\cm\IG_cm_backtestlab_<voice>.mp4`。ステージ voice→overlay→build（`all`で全部）。
- カット尺は音声に自動追従（`eff_lengths()` + `tpad`）。BGM `_CM_BGM`（無ければフォールバック、音量0.13）。
- 音声WAVの実体は `audio\reels\cm\`。

---

## 3. Reel のビルド `tools/make_reel_*.py`＋`make_reels_netacho.py`
- 縦1080x1920/30fps。共通基盤 `make_reels_netacho.py` が定数・ヘルパを export
  （W/H/FPS/色/`txt`/`fit`/`font`/`base_frame`/`dur`/`FF`/`BGM`/`AUDIO`/`OUTDIR`）。
- 各 `make_reel_<name>.py` は `NARR`/`DRAW`/`PAD` を持ち `[md|audio|render|build|all]` で動く。
- `fit()` が文字を自動縮小。安全域は上420px〜下1500px。

---

## 4. ブランド・表記ルール（共通）
- 配色: 濃紺 `#0d1426`（CM系は `#070912`）/ シアン `#00d4ff` / ゴールド `#d4af37`。
- 名称は必ず「**競艇｜バックテストLAB**」。選手名には「選手」を付ける（例: 峰竜太選手）。
- 必須の但し書き:「※的中・利益を保証するものではありません」「舟券の購入は20歳になってから」。
- 実在選手を否定的に名指ししない。ネガ題材は中立表現（「苦戦しやすい」等）。

---

## 5. 別PC / 別AI への渡し方（重要）

**このディレクトリ（lecture-factory）は git 管理外です。**
つまり `boatrace-analysis` リポジトリを別PCで pull しても、**この動画ツール一式は届きません。**
届くのは `docs/marketing/` の中の指示書（このファイル等）だけです。

別PCで動画を作らせるには、次のどちらかが必要:
1. **lecture-factory フォルダごと別PCへコピー**しておく（＝ツール本体を持たせる）。
   その上で「このPCの `C:\boat_project\lecture-factory\AGENTS.md` を読んで」と指示する。
2. ツールを持たせず、この指示書だけを渡して**スクリプトを書き起こさせる**（非推奨・再現性が低い）。

**別AIへの1行指示テンプレ:**
> 「`C:\boat_project\lecture-factory\AGENTS.md` を読んで、`python tools/make_set.py <題材キー> --no-upload` で
> 4点セット（本編・ショート・サムネ2枚）を作って。アップは非公開のみ、公開はしないで。」

`lecture-factory\AGENTS.md` はこのファイルと同じ内容の写し。**両方を同じ内容に保つこと。**

---

## 6. 注意（コスト・安全）
- キー（`gemini_key.txt` / `secrets/`）を絶対に出力・コミットしない。
- 動画生成は時間がかかる（本編で数分）。長い処理はバックグラウンド実行推奨。
- Gemini は従量課金。大量ループ生成はコスト直結。必要最小限の本数で。
- YouTube アップは private 既定。公開はリッキーさんが押す。

---

## 7. プロモーション戦略の仕様
コンテンツ5本柱・声/オープニングのマッピング・台本ルール・投稿ローテ・計測ログは
`PROMO_STRATEGY_HANDOFF.md` を参照。
