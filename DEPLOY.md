# Deployment Guide

本プロジェクトを公開する際の手順とオプション。

## 公開前のセキュリティチェックリスト

- [ ] `.env` が `.gitignore` に入っており、リポジトリに含まれていない
- [ ] `BOATRACE_WEB_SECRET` を **長いランダム文字列** に変更
  ```bash
  python -c "import secrets;print(secrets.token_hex(32))"
  ```
- [ ] `BOATRACE_MEMBER_PASSWORD` / `BOATRACE_PRO_PASSWORD` を本番用に変更
- [ ] `config.py:USER_AGENT` に**自分の連絡先 URL** を設定 (スクレイピング先への礼儀)
- [ ] `data/` `models_artifacts/` `logs/` がコミットされていない
- [ ] 個人を特定できる情報 (内部メモ、個人 email) が `notebooks/` `logs/` に残っていない

## オプション 1: Render / Railway (個人利用、最速)

### 構成
- **App**: Flask (gunicorn 化推奨)
- **DB**: SQLite (永続ディスク必要)
- **オッズスケジューラ**: 別 worker 起動

### Render 例 (`render.yaml`)

```yaml
services:
  - type: web
    name: boatrace-web
    env: python
    buildCommand: pip install -r requirements.txt gunicorn
    startCommand: gunicorn -w 2 -b 0.0.0.0:$PORT 'src.web.app:create_app()'
    envVars:
      - key: BOATRACE_WEB_SECRET
        generateValue: true
      - key: BOATRACE_MEMBER_PASSWORD
        sync: false
      - key: BOATRACE_PRO_PASSWORD
        sync: false
    disk:
      name: data
      mountPath: /opt/render/project/data
      sizeGB: 5

  - type: worker
    name: odds-scheduler
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: python scripts/odds_scheduler.py --daemon --interval 60
```

### コスト感
- Free tier: 試用は可能だが Sleep する
- Starter ($7/月): 常時稼働、5GB ディスク

## オプション 2: VPS + Cloudflare Tunnel (本格運用)

### 構成
- **VPS**: ConoHa / さくら / Vultr ($5-10/月)
- **Tunnel**: Cloudflare Tunnel (無料、DDoS 保護)
- **Auth (任意)**: Cloudflare Access で会員ゲート
- **DB**: SQLite + 日次バックアップ

### 手順概要

```bash
# サーバ側
git clone https://github.com/tsuyoshiyasuda0105/boatrace-analysis.git
cd boatrace-analysis
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt gunicorn

# .env 設定 (本番値)
cp .env.example .env
nano .env

# systemd 化 (例)
sudo cp deploy/boatrace-web.service /etc/systemd/system/
sudo systemctl enable --now boatrace-web

# odds scheduler
sudo cp deploy/boatrace-odds.service /etc/systemd/system/
sudo systemctl enable --now boatrace-odds

# Cloudflare Tunnel
cloudflared tunnel login
cloudflared tunnel create boatrace
# ~/.cloudflared/config.yml で 127.0.0.1:5050 → ホスト名 マッピング
cloudflared tunnel route dns boatrace your-domain.com
sudo cloudflared service install
```

### Pro プランを有料化する場合
- **Stripe** で課金統合
- 月 ¥980 / ¥2,980 等のサブスク
- 課金成功フラグを DB に書く `users` テーブルを追加
- `auth.py:is_pro()` を DB lookup に変更

## オプション 3: コードのみ公開 (ホスティングしない)

GitHub にコードのみ置いて、各ユーザーが自分で動かす。
最もシンプルで責任問題も少ない。README の「クイックスタート」だけで完結。

## 法律・倫理面の注意

### 必須
- **特商法表記**: 有料化する場合は事業者情報・連絡先・解約規約を表示
- **プライバシーポリシー**: 課金 = 個人情報、保護義務
- **利用規約**: 「投資ツールではない」「結果は保証しない」を明記
- **年齢確認**: 公営競技ベットは 20歳以上のみ

### 推奨
- ギャンブル依存症相談窓口へのリンクをフッターに常設
- 月の利用上限設定 (自主規制)
- 「賭けない方が良い」助言の積極表示
- "+EV ある!" のような誇大表現禁止

### 避けるべきこと
- 「絶対勝てる」「年間 +213% 回収」のような主張 (景品表示法に抵触)
- 推奨組合せの「保証」表現
- 高額情報商材化 (詐欺的になりやすい)

## モニタリング

```bash
# サーバー稼働
curl https://your-domain/healthz

# DB サイズチェック
ls -lh data/boatrace.db

# odds_trifecta が更新されているか
sqlite3 data/boatrace.db "SELECT MAX(recorded_at) FROM odds_trifecta"
```

## バックアップ

```bash
# 日次 (cron)
sqlite3 data/boatrace.db ".backup /backup/boatrace-$(date +%Y%m%d).db"
# S3 / B2 等にアップロード推奨
```

## トラブルシュート

| 症状 | 原因 | 対処 |
|---|---|---|
| Flask が再起動しない | Python キャッシュ | `find . -name __pycache__ -exec rm -rf {} +` |
| odds 取得失敗 | レート制限 / IP ブロック | `REQUEST_INTERVAL_SECONDS` を増やす |
| SQLite ロック | 書き込み競合 | WAL モードを確認 (`PRAGMA journal_mode=WAL`) |
| メモリ不足 | LightGBM 同時推論 | gunicorn workers を減らす |

## Public Release Checklist

- [ ] README.md の「your-user」を実際の GitHub アカウント名に置き換え
- [ ] LICENSE のコピーライト年 / 名前確認
- [ ] `.env.example` のコメントが分かりやすい
- [ ] GitHub Issue/PR テンプレート (任意)
- [ ] [GitHub Topics](https://github.com/topics) に `boatrace` `lightgbm` `flask` `japanese-public-gambling` 等
- [ ] スクリーンショットを `docs/screenshots/` に追加 (任意)
