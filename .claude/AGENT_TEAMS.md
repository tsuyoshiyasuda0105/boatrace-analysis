# エージェントチーム運用ガイド

このプロジェクトでは Claude Code の **エージェントチーム機能** (実験的) を
有効化しています。複数の Claude Code インスタンスを並列に走らせて、調査・
レビュー・新機能開発などを協調作業させられます。

## 立ち上げ手順

### 1. 機能有効化 (済)

`.claude/settings.json` に下記を設定済:

```json
{
  "env": {
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"
  },
  "teammateMode": "in-process"
}
```

`in-process` は tmux 不要で **任意のターミナル**で動きます。本プロジェクトを
Claude Code で開けば自動的に有効化されます。

### 2. 動作要件

- Claude Code **v2.1.32 以降** (確認済: v2.1.128)
- 設定変更後は Claude Code を **再起動**して反映

### 3. 確認

新しい Claude Code セッションを開いてから:

```
エージェントチームを 3 人作って、それぞれ UX / 性能 / セキュリティの
観点から /races ページを評価してください
```

と頼めば、3 人のチームメンバーが生成され、並列にレビューします。

---

## プリセットされたサブエージェント (5 種類)

このプロジェクトでは `.claude/agents/` に **5 つのドメイン専門エージェント**
を定義済みです。チームメンバー生成時に名前で参照できます。

| Agent | 担当 | 召喚例 |
|---|---|---|
| **data-collector** | Open API + スクレイピング | "data-collector エージェントを起動して、新しい結果取得 API を追加してください" |
| **ml-engineer** | LightGBM + cascade モデル | "ml-engineer を呼んで、wind_direction の重要度を再評価する策を検討して" |
| **web-developer** | Flask + Jinja2 + CSS | "web-developer に PC 用の幅広レイアウトを設計させて" |
| **scheduler-ops** | Task Scheduler + bat + VBS | "scheduler-ops に新しい 30 分毎タスクを設計依頼して" |
| **db-optimizer** | Supabase + SQLite + 接続最適化 | "db-optimizer を呼んで course1_stats を高速化して" |

## 推奨ユースケース例

### A. 並列コードレビュー (低リスク、おすすめ初トライ)

```
PR #N をレビューするチームを作って、3 人並列で動かしてください:
- web-developer 型: UX / アクセシビリティ
- db-optimizer 型: パフォーマンス影響
- ml-engineer 型: モデル前提が崩れていないか
```

### B. パフォーマンス調査 (複数仮説の同時検証)

```
/api/market-signals がまだ 7.76s 遅い原因を仮説別に並列調査するチームを
作って:
- 仮説 1: course1_stats CTE 自体が遅い → db-optimizer 担当
- 仮説 2: SSL handshake の累積 → scheduler-ops 担当
- 仮説 3: Python 側のループ処理 → ml-engineer 担当
互いの理論を反証し合って収束させてください
```

### C. クロスレイヤー新機能

```
「展示タイム順位を 1 号艇に対する優位として可視化」機能を実装するチームを
作って:
- ml-engineer: 特徴量と検証ロジック
- web-developer: UI/UX 表示
- data-collector: 展示タイム取得の信頼性確認
plan 承認モードで動かしてください
```

## ベストプラクティス (このプロジェクト特有)

1. **3-5 人で始める** — 公式ガイドの推奨。 4 人がほどよい。
2. **plan 承認モード** を併用すると DB schema 変更等のリスク高い作業の暴走を抑止できる
3. **トークンコストはチーム人数 × コンテキスト** で線形にスケール。
   ルーチン作業 (バグ修正 1 箇所等) は単一セッションが安い
4. **チームを終わらせるときは必ず "Clean up the team"** をリーダーに指示
5. **`CLAUDE.md` は全メンバー共通** — プロジェクト共通の知識はここに、
   ドメイン特有の知識は `.claude/agents/*.md` に
6. **ファイル競合回避** — 同一ファイル編集は明示的に分担を指示

## 制限事項 (公式ドキュメント参照)

- `/resume` / `/rewind` は in-process チームメンバーを復元しない
- 1 セッション = 1 チームのみ (ネスト不可)
- 権限は生成時に固定
- 分割ペインモードは Windows Terminal で不可 (in-process 設定済なので影響なし)

詳細: https://code.claude.com/docs/ja/agent-teams
