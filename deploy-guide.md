# Fly.io デプロイガイド：リアルタイム音声アシスタント

## 概要

このガイドでは、OpenAI RealtimeAPI + Twilio を使用した音声アシスタントアプリケーションを Fly.io にデプロイする方法を説明します。このアプリケーションは常時稼働状態を維持し、電話がかかってきた際にリアルタイムで応答します。

## 前提条件

- Fly.io アカウント
- Flyctl CLI (`brew install flyctl` でインストール)
- OpenAI API キー
- Twilio アカウントと電話番号

## 1. 初期セットアップ

### Fly.io へのログイン

```bash
flyctl auth login
```

### アプリケーションの初期化

プロジェクトディレクトリで以下を実行:

```bash
flyctl launch
```

この過程で:
- アプリケーション名を選択（または自動生成）
- リージョンを選択（`nrt` for Tokyo など）
- Postgres など追加サービスはスキップ
- 初回デプロイはスキップ可能（後で行う）

## 2. 主要な設定ファイル

### fly.toml

最も重要な設定ファイルです。主な設定ポイント:

```toml
# アプリ名と基本情報
app = "speech-assistant-openai-realtime-api-python"
primary_region = "nrt"

# 環境変数（公開可能なもの）
[env]
  PORT = "5050"

# HTTP/WebSocketサービス設定
[http_service]
  internal_port = 5050
  force_https = true
  # 重要: アプリを常時稼働させる設定
  auto_stop_machines = "off"
  auto_start_machines = false
  min_machines_running = 1
  processes = ["app"]

# VM設定
[[vm]]
  memory = "1gb"
  cpu_kind = "shared"
  cpus = 1
```

### Dockerfile

アプリケーションのコンテナ化に使用:

```dockerfile
FROM python:3.10.11 AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN python -m venv .venv
COPY requirements.txt ./
RUN .venv/bin/pip install -r requirements.txt
FROM python:3.10.11-slim
WORKDIR /app
COPY --from=builder /app/.venv .venv/
COPY . .
EXPOSE 5050
CMD ["/app/.venv/bin/python", "main.py"]
```

## 3. 機密情報の設定

アプリケーションの機密情報（API キーなど）は環境変数として設定:

```bash
# OpenAI APIキーを設定
flyctl secrets set OPENAI_API_KEY=sk_xxxxxxxxxxxx

# 必要に応じてTwilio認証情報を設定
flyctl secrets set TWILIO_ACCOUNT_SID=ACxxxxxxx
flyctl secrets set TWILIO_AUTH_TOKEN=xxxxxxx
```

## 4. デプロイ

設定が完了したら、アプリケーションをデプロイ:

```bash
flyctl deploy
```

デプロイが完了したら、アプリケーションのステータスを確認:

```bash
flyctl status
```

ログを確認:

```bash
flyctl logs
```

## 5. 常時稼働設定の重要ポイント

WebSocketサーバーを常時稼働させるための重要な設定:

1. **自動停止を無効化**: 
   ```toml
   auto_stop_machines = "off"
   auto_start_machines = false
   ```

2. **最小実行マシン数の設定**:
   ```toml
   min_machines_running = 1
   ```

3. **必要な台数の調整**:

   マシンが2台ある場合、1台に減らす:
   ```bash
   fly scale count 1
   ```

   増やしたい場合:
   ```bash
   fly scale count 2
   ```

## 6. Twilio との連携

### Twilio 側の設定

1. Twilio コンソールで電話番号の設定を開く
2. Voice Configuration セクションで:
   - A CALL COMES IN で Webhook を選択
   - URL: `https://あなたのアプリ名.fly.dev/incoming-call`
   - HTTP Method: POST

### TwiML Application を使用する場合

```bash
# TwiML App 作成
twilio api:core:applications:create \
  --friendly-name "Steakhouse-AI" \
  --voice-url "https://あなたのアプリ名.fly.dev/incoming-call" \
  --voice-method POST

# 電話番号を紐付け
twilio phone-numbers:update "+1XXXXXXXXXX" \
  --voice-application-sid APXXXXXXXXXX
```

## 7. デプロイの確認

### エンドポイントの確認

1. メインエンドポイント:
   ```bash
   curl https://あなたのアプリ名.fly.dev/
   # 期待される応答: {"message":"Twilio Media Stream Server is running!"}
   ```

2. Webhook エンドポイント:
   ```bash
   curl -I https://あなたのアプリ名.fly.dev/incoming-call
   # HTTP/2 405 と allow: GET, POST ヘッダーが表示されるはず
   ```

### ログの監視

```bash
flyctl logs
```

ログで "Uvicorn running on http://0.0.0.0:5050" が表示されていれば正常に起動しています。

## 8. トラブルシューティング

### アプリが「Suspended」状態になる場合

1. fly.toml の設定を確認:
   ```toml
   auto_stop_machines = "off"
   auto_start_machines = false
   min_machines_running = 1
   ```

2. マシンを再起動:
   ```bash
   fly machines list    # マシンIDを確認
   fly machine restart <マシンID>
   ```

3. 必要に応じてマシンをスケール:
   ```bash
   fly scale count 1    # マシン数を1に設定
   ```

### デプロイエラー

1. ログを確認:
   ```bash
   flyctl logs
   ```

2. 依存関係の問題の場合は、requirements.txt を更新してから再デプロイ

### WebSocket接続の問題

1. Twilio WebSocketは HTTP/1.1 経由で接続するため、直接curlでのテストは404になることがあります
2. 実際のTwilio接続で問題がある場合は、ログで具体的なエラーを確認

## 9. 参考コマンド

```bash
# アプリステータス確認
fly status

# ログ確認
fly logs

# シークレット設定
fly secrets set KEY=VALUE

# マシン一覧表示
fly machines list

# マシン再起動
fly machine restart <ID>

# スケール調整
fly scale count <数>
fly scale vm <サイズ>
fly scale memory <メモリMB>

# コンソール接続
fly ssh console
```

## 10. リソース

- [Fly.io ドキュメント](https://fly.io/docs/)
- [Twilio TwiML ドキュメント](https://www.twilio.com/docs/voice/twiml)
- [OpenAI Realtime API ドキュメント](https://platform.openai.com/docs/api-reference/realtime) 