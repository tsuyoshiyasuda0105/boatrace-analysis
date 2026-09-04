# AGENTS.md — 競艇動画ファクトリー 引き継ぎ

このディレクトリ (`C:\boat_project\lecture-factory\`) は「競艇｜バックテストLAB」の
**マーケ動画（CM・インスタReel・YouTube講義）** を作る作業場です。
AI アシスタント（Claude / **CODEX** など）が共通で作業できるよう要点をまとめます。

---

## 0. 実行環境（重要）

- Python は **boatrace 側の venv** を使う（このディレクトリ専用の venv は無い）:
  `C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe`
- FFmpeg (Gyan 9.0):
  `C:\Users\tsuyo\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0-full_build\bin\ffmpeg.exe`
  （`ffprobe.exe` も同ディレクトリ）
- OS: Windows 11 / PowerShell。日本語出力は `$env:PYTHONIOENCODING="utf-8"`。
- ffmpeg に渡すファイル名は英数字推奨（Git Bash で日本語名は Illegal byte sequence になりがち）。

---

## 1. 音声ナレーションは Gemini TTS に移行（2026-08-29〜）

**方針変更**: CM・看板動画のナレーションは **Google Gemini TTS** を使う。
（以前は edge-tts `ja-JP-KeitaNeural`。普段のReel量産は今も edge-tts でよい＝下記「使い分け」）

### 確定した声（この2種を標準とする）
| 用途 | 声名 | 印象 |
|------|------|------|
| 男性ナレーション | **Charon** | 落ち着いた低め・王道 |
| 女性ナレーション | **Callirrhoe** | 自然体・リラックス |

他の声は `python tools/gemini_tts.py --list` で一覧。全30種、どれも日本語を話せる。

### ツール: `tools/gemini_tts.py`
```powershell
# 1行生成:  python tools/gemini_tts.py "台詞" 出力.wav [声名]
& C:\boat_project\boatrace-analysis\.venv\Scripts\python.exe tools\gemini_tts.py "テスト" out.wav Charon
# 声一覧:   python tools/gemini_tts.py --list
# キー確認: python tools/gemini_tts.py --check
```
- Python から使う場合: `import gemini_tts; gemini_tts.synth(text, wav_path, "Charon")`
- 出力は **24kHz / mono / 16bit WAV**（合成前に ffmpeg で `-ar 44100 -ac 1` に揃えると既存パイプラインと整合）。
- **429（レート制限）自動リトライ内蔵**（1分制限は吸収する。日次上限は吸収不可）。

### API キーの扱い（機密）
- キーは **`C:\boat_project\lecture-factory\gemini_key.txt`** の1行目から自動読込。
  環境変数 `GEMINI_API_KEY` があればそちらを優先。
- **キーの中身をログ・チャット・コミットに出さないこと。** `.gitignore` で除外済み。
- 発行元: Google AI Studio (`https://aistudio.google.com/apikey`)。

### 料金・上限
- **課金(billing)有効化済み**（2026-08-29）。無料枠時代の厳しい上限
  （**1分3リクエスト＋1日10リクエスト**）は緩和されている。
- とはいえ従量課金。CM等の看板動画に限定して使うのが安全。使用量は Google Cloud の課金ダッシュボードで確認。

### 使い分け方針
- **普段のReel量産** → edge-tts `ja-JP-KeitaNeural`（無制限・無料・クレジット表記不要）
- **CM・看板動画** → Gemini（Charon / Callirrhoe）

---

## 2. CM のビルド: `tools/build_cm.py`

```powershell
# Gemini の声で:  --voice gemini:<声名>
& ...\.venv\Scripts\python.exe tools\build_cm.py all --voice gemini:Charon
& ...\.venv\Scripts\python.exe tools\build_cm.py all --voice gemini:Callirrhoe
# edge-tts の声で: --voice ja-JP-KeitaNeural（プレフィックス無し）
```
- `--voice gemini:Name` で Gemini ルート、プレフィックス無しは edge-tts。
- 出力: **`out\cm\IG_cm_backtestlab_<voice>.mp4`**（声ごとに別名。charon / callirrhoe / keita）。
- ステージ: `voice`（音声）→ `overlay`（文字レイヤPNG）→ `build`（合成）。`all` で全部。
- **カット尺は自動で音声に合わせて延長**（`eff_lengths()`。語尾が切れないよう `tpad` で映像を静止延長）。
- CUTS はソースに依存: AI生成カット `C:\Users\tsuyo\Downloads\cut{1,2,3,5}_1080x1920.mp4` と
  実画面録画 `slides\rec\cm_screen.webm`。
- **BGM**: `_CM_BGM`（`...\OneDrive\デスクトップ\競艇動画\music\こたえのさきへ (1).mp3`）を
  頭から使用しフェードイン/アウト。無ければ `make_reels_netacho.BGM` にフォールバック。音量 0.13（控えめ）。
  **`--no-bgm` を付けるとBGMを敷かずナレーションのみ**で仕上げる（例:
  `build_cm.py all --voice gemini:Charon --no-bgm`）。
- 音声WAVの実体は **`audio\reels\cm\`**（`make_reels_netacho.AUDIO` が `audio\reels` を指すため。`audio\cm` ではない）。

---

## 3. Reel のビルド: `tools/make_reel_*.py` と `make_reels_netacho.py`

- 縦 1080x1920 / 30fps。共通基盤 `make_reels_netacho.py` が定数・ヘルパを export
  （W/H/FPS/色/`txt`/`fit`/`font`/`base_frame`/`dur`/`FF`/`BGM`/`AUDIO`/`OUTDIR` 等）。
- 各 `make_reel_<name>.py` は `NARR`（台詞）/`DRAW`（各シーン描画）/`PAD` を持ち、
  `[md|audio|render|build|all]` ステージで動く。
- 現状ナレーションは `gen_audio()` 内で edge-tts `ja-JP-KeitaNeural` (pitch -6Hz)。
  **Gemini に切り替えたい場合**は `gen_audio()` を `gemini_tts.synth(text, wav, "Charon")` に差し替え、
  生成後 ffmpeg で 44100/mono に揃える（build_cm.py の Gemini 分岐が実装例）。
- `fit()` は文字がはみ出さないようフォントを自動縮小。安全域は上420px〜下1500px。

---

## 4. ブランド・表記ルール（動画共通）

- 配色: 濃紺 `#070912` / シアン `#00d4ff` / ゴールド `#d4af37`。
- 名称は必ず「**競艇｜バックテストLAB**」。選手名には必ず「選手」を付ける（例: 峰竜太選手）。
- 数字は10年・約55万レースの実測に基づくこと（DB: `data/kachisuji_search.db`）。誇張しない。
- 必須の但し書き: 「※的中・利益を保証するものではありません」「舟券の購入は20歳になってから」。
- 読みの既知修正: 茅原=かやはら / 毒島=ぶすじま。「近日公開です」の抑揚は要調整項目（未確定）。

---

## 5. CODEX への注意

- キー (`gemini_key.txt`) を **絶対に出力・コミットしない**。
- 動画生成は時間がかかる（CMで数分）。長い処理はバックグラウンド実行推奨。
- Gemini 課金が有効なので、**大量ループ生成はコストに直結**。必要最小限の本数で。
- このディレクトリは（現状）git 管理外。もし git 化するなら `.gitignore` を尊重すること。

---

## 6. プロモーション戦略の実装仕様（2026-09-04 追加）

コンテンツ5本柱・柱→声/オープニングのマッピング・台本ルール（人の視点1文・禁止語・
但し書き）・合成ラベル・投稿ローテ・計測ログの仕様は **`PROMO_STRATEGY_HANDOFF.md`** を参照。
Codex 実装済みの声切替・エンディングはそのまま使い、`PILLAR` 宣言で解決する形に接続する。
