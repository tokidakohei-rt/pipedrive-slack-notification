# Pipedrive パイプライン自動レポート Slack 通知

Pipedriveの特定パイプライン内にある全ステージの案件（企業）一覧を取得し、毎日決まった時間にSlackに自動投稿するシステムです。

## 機能

- 指定したパイプラインの全ステージ情報を取得
- 各ステージの`status=open`のDeal一覧を取得
- Dealの`title`フィールドを企業名として使用
- ステージごとに企業名をグルーピング（重複除外）
- PipedriveのWebhookと連携し、新規カードや特定ステージ到達を即時通知
- 毎日09:00（JST）にSlackチャンネルに自動投稿

## セットアップ

### 1. リポジトリのクローン

```bash
git clone https://github.com/tokidakohei-rt/pipedrive-slack-notification.git
cd pipedrive-slack-notification
```

### 2. GitHub Secretsの設定

リポジトリのSettings > Secrets and variables > Actionsから、以下のSecretsを追加してください：

| Secret 名              | 説明                         | 取得方法                                    |
| --------------------- | -------------------------- | --------------------------------------- |
| `PIPEDRIVE_API_TOKEN` | Pipedrive個人APIトークン          | Pipedrive > Settings > Personal > API > Your personal API token |
| `PIPELINE_ID`         | 対象のパイプラインID               | PipedriveのパイプラインURLから取得（例: `https://company.pipedrive.com/pipeline/123` の `123`） |
| `SLACK_WEBHOOK_URL`   | Slack Incoming Webhook URL | Slack > Apps > Incoming Webhooks > Add to Slack > Webhook URL |

### 3. 営業担当者メンション設定

`config/owner_slack_map.sample.yaml` をコピーして `config/owner_slack_map.yaml` を作成し、Pipedriveの `deal.owner_id` と SlackユーザーIDの対応を記述してください。

```bash
cp config/owner_slack_map.sample.yaml config/owner_slack_map.yaml
```

ファイル例:

```yaml
# キー: Pipedrive owner_id / 値: SlackユーザーID
123: UAAA1111
456: UBBB2222
```

本ファイルは新規カード通知や「agent調整完了」ステージ移動時のメンションに利用されます。SlackユーザーIDは `@ユーザー名` ではなく `U` から始まる固有IDを使用してください。

### 4. Slackスレッド追跡（任意）

Slackのスレッドに後続通知をまとめたい場合は、PipedriveのDealにカスタムフィールド（テキスト）を作成してそのフィールドキーを `SLACK_THREAD_TS_FIELD_KEY` に設定してください。

1. Pipedrive管理画面で Deal のカスタムフィールドを追加（例: フィールド名「Slack thread ts」）
2. フィールド詳細に表示される「フィールドキー」（例: `abcd1234efgh5678`）を控える
3. 環境変数 `SLACK_THREAD_TS_FIELD_KEY=abcd1234efgh5678` を設定してデプロイ
4. 取引作成通知で取得したSlackの `thread_ts` がPipedrive側に保存され、ステータス更新時は同じスレッドへ返信、見つからない場合のみ新規投稿します

設定しない場合は従来どおりチャンネルに新規投稿します。

### 5. Pipedrive Webhook設定

1. Pipedrive管理画面の **Tools > Webhooks** で新規Webhookを作成
2. URL にデプロイ済みエンドポイント（例: `https://example.com/api/deal-ui`）を指定
3. イベントは **Deals → added / updated** を選択（`updated` はステージ移動時に発火）
4. 今回はBasic認証等を利用していないため、追加設定は不要

追加の環境変数（任意）:

| 変数名 | 役割 | 補足 |
| --- | --- | --- |
| `AGENT_READY_STAGE_NAME` | 「agent調整完了」に相当するステージ名 | 省略時は `agent調整完了` |
| `OWNER_SLACK_MAP_PATH` | 対応表ファイルのパス | 省略時は `config/owner_slack_map.yaml` |
| `SLACK_THREAD_TS_FIELD_KEY` | DealにSlack thread tsを保存するフィールドキー | 省略時はスレッド追跡を無効化 |
| `AGENT_FIXED_MENTIONS` | カード作成時に固定でメンションするSlack ID（カンマ区切り） | 省略時は `U07PC1CSXH8,U03HP6CM1FB` |
| `HANDOVER_DATE_FIELD_KEY` | 引き渡し希望日フィールドのキー | 省略時はデフォルトキー `b459...9566` |
| `COUPON_SPREADSHEET_URL` | クーポン共有スプレッドシートのURL | 省略時は社内デフォルトURL |
| `EARLY_NOTIFY_STAGE_NAMES` | カード追加時に固定メンションをcc扱いにするステージ名（カンマ区切り） | 省略時は `商談セット,Chat導入検討` |
| `CHAT_APPROVAL_STAGE_NAME` | 「Chat導入内諾」通知を送るステージ名 | 省略時は `Chat導入内諾` |

### 6. 動作確認

#### 手動実行

GitHub Actionsのワークフローを手動実行して動作確認できます：

1. リポジトリの「Actions」タブを開く
2. 「Daily Pipeline Report」ワークフローを選択
3. 「Run workflow」ボタンをクリック
4. 実行ログを確認し、Slackにメッセージが投稿されることを確認

#### ローカルでのテスト（オプション）

ローカル環境でテストする場合：

```bash
# 依存パッケージのインストール
pip install -r requirements.txt

# 環境変数を設定して実行
export PIPEDRIVE_API_TOKEN="your-api-token"
export PIPELINE_ID="your-pipeline-id"
export SLACK_WEBHOOK_URL="your-webhook-url"
python main.py
```

## 実行スケジュール

- **実行時刻**: 毎日 09:00（JST）
- **実行方法**: GitHub Actionsのcronジョブ（UTC 00:00 = JST 09:00）

## 出力例

Slackに以下のようなメッセージが投稿されます：

```
本日のPipedriveパイプライン状況

【ステージ: リード】
・株式会社AAA
・株式会社BBB

【ステージ: 商談中】
・株式会社CCC

【ステージ: 見積済み】
・該当なし
```

## ファイル構成

```
.
├── main.py                                    # メインスクリプト
├── requirements.txt                           # 依存パッケージ
├── .github/
│   └── workflows/
│       └── daily-pipeline-report.yml         # GitHub Actionsワークフロー
└── README.md                                  # このファイル
```

## トラブルシューティング

### Slackにメッセージが投稿されない

- GitHub Secretsが正しく設定されているか確認
- GitHub Actionsの実行ログでエラーがないか確認
- Slack Incoming Webhook URLが有効か確認

### Pipedrive APIエラー

- APIトークンが有効か確認
- パイプラインIDが正しいか確認
- Pipedrive APIのレート制限に達していないか確認

### 企業名が取得できない

- Dealの`title`フィールドに値が設定されているか確認
- `status=open`のDealが存在するか確認

## ライセンス

このプロジェクトはMITライセンスの下で公開されています。

