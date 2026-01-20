#!/usr/bin/env python3
"""
Pipedrive パイプライン自動レポート Slack 通知スクリプト

指定されたパイプラインの全ステージの案件（企業）一覧を取得し、
LLMでサマリを生成して、Slackに投稿する。
- 親メッセージ: LLMによるパイプラインサマリ
- スレッド: ステージごとの詳細リスト
"""

import os
import sys
import logging
from typing import Dict, List, Set, Optional, Any
import requests
import google.generativeai as genai

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 環境変数から設定を取得
PIPEDRIVE_API_TOKEN = os.getenv('PIPEDRIVE_API_TOKEN')
PIPELINE_ID = os.getenv('PIPELINE_ID')
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_CHANNEL = os.getenv('SLACK_CHANNEL')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# 後方互換性: SLACK_WEBHOOK_URLが設定されている場合はレガシーモード
SLACK_WEBHOOK_URL = os.getenv('SLACK_WEBHOOK_URL')

# Pipedrive API設定
PIPEDRIVE_API_BASE = 'https://api.pipedrive.com/v1'


def validate_env_vars():
    """環境変数の検証"""
    if not PIPEDRIVE_API_TOKEN:
        logger.error('PIPEDRIVE_API_TOKEN が設定されていません')
        sys.exit(1)
    if not PIPELINE_ID:
        logger.error('PIPELINE_ID が設定されていません')
        sys.exit(1)
    
    # 新モード: Slack Bot API + LLM
    if SLACK_BOT_TOKEN and GEMINI_API_KEY:
        if not SLACK_CHANNEL:
            logger.error('SLACK_CHANNEL が設定されていません')
            sys.exit(1)
        return 'enhanced'
    
    # レガシーモード: Webhook
    if SLACK_WEBHOOK_URL:
        logger.info('レガシーモード: SLACK_WEBHOOK_URL を使用します（LLMサマリなし）')
        return 'legacy'
    
    logger.error('SLACK_BOT_TOKEN + SLACK_CHANNEL + GEMINI_API_KEY、または SLACK_WEBHOOK_URL が必要です')
    sys.exit(1)


def get_pipeline_stages(pipeline_id: str) -> List[Dict]:
    """
    パイプラインのステージ一覧を取得
    
    Args:
        pipeline_id: パイプラインID
        
    Returns:
        ステージ情報のリスト
    """
    # まずパイプライン情報を取得してステージを取得する方法に戻す
    url = f'{PIPEDRIVE_API_BASE}/pipelines/{pipeline_id}'
    params = {'api_token': PIPEDRIVE_API_TOKEN}
    
    try:
        logger.info(f'パイプライン情報を取得中: pipeline_id={pipeline_id}')
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        logger.debug(f'Pipedrive API レスポンス: {data}')
        
        if not data.get('success'):
            error_msg = data.get('error', 'Unknown error')
            logger.error(f'Pipedrive API エラー: {error_msg}')
            logger.error(f'レスポンス全体: {data}')
            sys.exit(1)
        
        pipeline_data = data.get('data')
        if not pipeline_data:
            logger.error(f'パイプライン {pipeline_id} のデータが見つかりませんでした')
            logger.error(f'レスポンス全体: {data}')
            sys.exit(1)
        
        # パイプライン情報からステージを取得
        stages = pipeline_data.get('stages', [])
        
        if not stages:
            logger.warning(f'パイプライン {pipeline_id} にステージが見つかりませんでした')
            logger.warning(f'パイプラインデータのキー: {list(pipeline_data.keys())}')
            # ステージが直接含まれていない場合、別の方法で取得を試みる
            logger.info('ステージがパイプライン情報に含まれていないため、/stagesエンドポイントから取得を試みます')
            return get_stages_by_pipeline_id(pipeline_id)
        
        # ステージをorder_nrでソート
        stages_sorted = sorted(stages, key=lambda x: x.get('order_nr', 0))
        
        logger.info(f'パイプライン {pipeline_id} から {len(stages_sorted)} 個のステージを取得')
        for i, stage in enumerate(stages_sorted, 1):
            logger.info(f'  ステージ {i}: id={stage.get("id")}, name={stage.get("name")}, order_nr={stage.get("order_nr")}')
        
        return stages_sorted
        
    except requests.exceptions.RequestException as e:
        logger.error(f'パイプライン情報の取得に失敗: {e}')
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f'レスポンスステータス: {e.response.status_code}')
            logger.error(f'レスポンスボディ: {e.response.text}')
        sys.exit(1)


def get_stages_by_pipeline_id(pipeline_id: str) -> List[Dict]:
    """
    /stagesエンドポイントからパイプラインIDでフィルタリングしてステージを取得
    
    Args:
        pipeline_id: パイプラインID
        
    Returns:
        ステージ情報のリスト
    """
    url = f'{PIPEDRIVE_API_BASE}/stages'
    params = {
        'api_token': PIPEDRIVE_API_TOKEN,
        'pipeline_id': pipeline_id
    }
    
    try:
        logger.info(f'/stagesエンドポイントからステージ情報を取得中: pipeline_id={pipeline_id}')
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        logger.debug(f'Pipedrive API レスポンス: {data}')
        
        if not data.get('success'):
            error_msg = data.get('error', 'Unknown error')
            logger.error(f'Pipedrive API エラー: {error_msg}')
            logger.error(f'レスポンス全体: {data}')
            return []
        
        stages = data.get('data', [])
        
        if not stages:
            logger.warning(f'パイプライン {pipeline_id} にステージが見つかりませんでした')
            return []
        
        # ステージをorder_nrでソート
        stages_sorted = sorted(stages, key=lambda x: x.get('order_nr', 0))
        
        logger.info(f'/stagesエンドポイントから {len(stages_sorted)} 個のステージを取得')
        for i, stage in enumerate(stages_sorted, 1):
            logger.info(f'  ステージ {i}: id={stage.get("id")}, name={stage.get("name")}, order_nr={stage.get("order_nr")}')
        
        return stages_sorted
        
    except requests.exceptions.RequestException as e:
        logger.error(f'/stagesエンドポイントからの取得に失敗: {e}')
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f'レスポンスステータス: {e.response.status_code}')
            logger.error(f'レスポンスボディ: {e.response.text}')
        return []


def get_deals_by_stage(pipeline_id: str, stage_id: str) -> List[Dict]:
    """
    指定ステージのopenなDeal一覧を取得
    
    Args:
        pipeline_id: パイプラインID
        stage_id: ステージID
        
    Returns:
        Deal情報のリスト
    """
    url = f'{PIPEDRIVE_API_BASE}/deals'
    params = {
        'api_token': PIPEDRIVE_API_TOKEN,
        'pipeline_id': pipeline_id,
        'stage_id': stage_id,
        'status': 'open',
        'limit': 500  # 最大取得件数
    }
    
    try:
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        if not data.get('success'):
            error_msg = data.get('error', 'Unknown error')
            logger.error(f'Pipedrive API エラー (stage_id: {stage_id}): {error_msg}')
            return []
        
        deals = data.get('data')
        
        # dataがNoneの場合やリストでない場合は空リストを返す
        if deals is None:
            logger.warning(f'ステージ {stage_id} のDealデータがNoneです')
            return []
        
        if not isinstance(deals, list):
            logger.warning(f'ステージ {stage_id} のDealデータがリストではありません: {type(deals)}')
            return []
        
        logger.info(f'ステージ {stage_id} から {len(deals)} 件のDealを取得')
        return deals
        
    except requests.exceptions.RequestException as e:
        logger.error(f'Deal情報の取得に失敗 (stage_id: {stage_id}): {e}')
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f'レスポンスステータス: {e.response.status_code}')
            logger.error(f'レスポンスボディ: {e.response.text}')
        return []


def group_companies_by_stage(pipeline_id: str, stages: List[Dict]) -> Dict[str, List[str]]:
    """
    ステージごとに企業名をグルーピング
    
    Args:
        pipeline_id: パイプラインID
        stages: ステージ情報のリスト
        
    Returns:
        ステージ名をキー、企業名のリスト（ソート済み）を値とする辞書
    """
    stage_companies: Dict[str, List[str]] = {}
    
    logger.info(f'ステージごとのDeal取得を開始: {len(stages)} ステージ')
    
    for stage in stages:
        stage_id = str(stage.get('id'))
        stage_name = stage.get('name', '不明')
        
        logger.info(f'ステージ "{stage_name}" (id: {stage_id}) のDealを取得中...')
        deals = get_deals_by_stage(pipeline_id, stage_id)
        companies = set()
        
        for deal in deals:
            title = deal.get('title', '').strip()
            if title:
                companies.add(title)
            else:
                logger.debug(f'Deal id={deal.get("id")} のtitleが空です')
        
        stage_companies[stage_name] = sorted(companies)
        logger.info(f'ステージ "{stage_name}": {len(companies)} 社')
        if companies:
            logger.debug(f'  企業名: {sorted(companies)}')
    
    return stage_companies


def generate_pipeline_summary(stage_companies: Dict[str, List[str]]) -> str:
    """
    LLM（Gemini）を使ってパイプラインのサマリを生成
    
    Args:
        stage_companies: ステージ名をキー、企業名のリストを値とする辞書
        
    Returns:
        生成されたサマリテキスト
    """
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    # パイプラインデータを整形
    pipeline_data_text = []
    total_companies = 0
    
    for stage_name, companies in stage_companies.items():
        count = len(companies)
        total_companies += count
        pipeline_data_text.append(f"- {stage_name}: {count}社")
        if companies:
            pipeline_data_text.append(f"  企業: {', '.join(companies)}")
    
    pipeline_info = "\n".join(pipeline_data_text)
    
    prompt = f"""以下は営業パイプラインの現在の状況です。このデータを分析し、営業チーム向けの具体的なサマリを日本語で作成してください。

## パイプライン状況
総企業数: {total_companies}社

{pipeline_info}

## サマリの要件
以下のフォーマットに従って出力してください:

```
🔢 【ステージ別状況】
[各ステージ名と件数を箇条書きで表示。最も案件が多いステージには「⭐」を付ける]

📌 【分析・注目ポイント】
・[案件が集中しているステージとその割合についてコメント]
・[ボトルネックになりそうな点や、進捗の偏りがあれば指摘]

💡 【本日のアクション】
・[優先的に対応すべきことを1-2点、具体的に提示]
```

## ルール
- ステージ名はデータに記載されている通り正確に使用する
- 数字は必ずデータから算出する
- 絵文字を適度に使って親しみやすく
- 400文字程度に収める
- 詳細はスレッドにあるので「詳細はスレッドをご確認ください」と最後に添える

サマリを出力してください（サマリ本文のみ、前置きや説明は不要）:"""

    logger.info('Gemini APIでサマリを生成中...')
    
    response = model.generate_content(prompt)
    
    summary = response.text.strip()
    logger.info(f'サマリ生成完了: {len(summary)} 文字')
    
    return summary


def format_stage_detail(stage_name: str, companies: List[str]) -> str:
    """
    ステージの詳細メッセージをフォーマット
    
    Args:
        stage_name: ステージ名
        companies: 企業名のリスト
        
    Returns:
        フォーマット済みメッセージ
    """
    if companies:
        companies_text = ' / '.join(companies)
        return f"*【{stage_name}】* ({len(companies)}社)\n{companies_text}"
    else:
        return f"*【{stage_name}】* (0社)\n該当なし"


def send_to_slack_with_thread(summary: str, stage_companies: Dict[str, List[str]]) -> bool:
    """
    Slack Bot APIでサマリを送信し、スレッドに詳細を投稿
    
    Args:
        summary: LLM生成のサマリ
        stage_companies: ステージ別の企業リスト
        
    Returns:
        送信成功時True
    """
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }
    
    # 総企業数を計算
    total_companies = sum(len(companies) for companies in stage_companies.values())
    
    # 親メッセージ: サマリ
    parent_message = f"📊 *本日のNEWT Chat パイプライン状況* ({total_companies}社)\n\n{summary}"
    
    payload = {
        'channel': SLACK_CHANNEL,
        'text': parent_message,
        'unfurl_links': False,
        'unfurl_media': False
    }
    
    try:
        # 親メッセージを送信
        logger.info('サマリメッセージを送信中...')
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            headers=headers,
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        
        if not data.get('ok'):
            logger.error(f'Slack API エラー: {data.get("error")}')
            return False
        
        thread_ts = data.get('ts')
        logger.info(f'サマリメッセージ送信完了: ts={thread_ts}')
        
        # スレッドに詳細を投稿
        logger.info('スレッドに詳細を投稿中...')
        for stage_name, companies in stage_companies.items():
            detail_message = format_stage_detail(stage_name, companies)
            
            thread_payload = {
                'channel': SLACK_CHANNEL,
                'text': detail_message,
                'thread_ts': thread_ts,
                'unfurl_links': False,
                'unfurl_media': False
            }
            
            thread_response = requests.post(
                'https://slack.com/api/chat.postMessage',
                headers=headers,
                json=thread_payload,
                timeout=30
            )
            thread_response.raise_for_status()
            thread_data = thread_response.json()
            
            if not thread_data.get('ok'):
                logger.warning(f'スレッド投稿エラー ({stage_name}): {thread_data.get("error")}')
            else:
                logger.info(f'  ステージ "{stage_name}" の詳細を投稿')
        
        logger.info('Slackへの投稿が完了しました')
        return True
        
    except requests.exceptions.RequestException as e:
        logger.error(f'Slackへの投稿に失敗: {e}')
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f'レスポンスステータス: {e.response.status_code}')
            logger.error(f'レスポンスボディ: {e.response.text}')
        return False


def format_slack_message_legacy(stage_companies: Dict[str, List[str]]) -> str:
    """
    レガシーモード用: Slackメッセージをフォーマット
    
    Args:
        stage_companies: ステージ名をキー、企業名のリストを値とする辞書
        
    Returns:
        フォーマット済みメッセージ
    """
    message_parts = ['本日のNEWT Chat パイプライン状況（※敬称略）\n']
    
    for stage_name, companies in stage_companies.items():
        message_parts.append(f'【{stage_name}】')
        
        if companies:
            # 企業名をソートして表示
            companies_line = ' / '.join(companies)
            message_parts.append(f'・{companies_line}')
        else:
            message_parts.append('・該当なし')
        
        message_parts.append('')  # 空行
    
    return '\n'.join(message_parts)


def send_to_slack_legacy(message: str) -> bool:
    """
    レガシーモード: Slack Incoming Webhookにメッセージを送信
    
    Args:
        message: 送信するメッセージ
        
    Returns:
        送信成功時True
    """
    payload = {'text': message}
    
    # デバッグ: メッセージ内容をログ出力（機密情報は含まない）
    logger.info(f'Slackに送信するメッセージ長: {len(message)} 文字')
    logger.debug(f'メッセージ内容: {message[:200]}...' if len(message) > 200 else f'メッセージ内容: {message}')
    
    try:
        response = requests.post(SLACK_WEBHOOK_URL, json=payload, timeout=30)
        
        # レスポンスの詳細をログ出力
        logger.info(f'Slack API レスポンス: ステータスコード={response.status_code}')
        logger.debug(f'レスポンスボディ: {response.text}')
        
        response.raise_for_status()
        
        # Slack Incoming Webhookは成功時に "ok" を返す
        if response.text.strip() == 'ok':
            logger.info('Slackへの投稿に成功')
            return True
        else:
            logger.warning(f'Slack APIが予期しないレスポンスを返しました: {response.text}')
            return True  # ステータスコードが200なら成功とみなす
        
    except requests.exceptions.RequestException as e:
        logger.error(f'Slackへの投稿に失敗: {e}')
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f'レスポンスステータス: {e.response.status_code}')
            logger.error(f'レスポンスボディ: {e.response.text}')
        return False


def main():
    """メイン処理"""
    logger.info('Pipedrive パイプライン自動レポート処理を開始')
    
    # 環境変数の検証
    mode = validate_env_vars()
    logger.info(f'動作モード: {mode}')
    
    # パイプラインのステージ一覧を取得
    stages = get_pipeline_stages(PIPELINE_ID)
    
    if not stages:
        logger.error('ステージが見つかりませんでした。以下を確認してください:')
        logger.error('1. PIPELINE_IDが正しいか確認')
        logger.error('2. パイプラインにステージが設定されているか確認')
        logger.error('3. APIトークンにパイプラインへのアクセス権限があるか確認')
        sys.exit(1)
    
    # ステージごとに企業名をグルーピング
    stage_companies = group_companies_by_stage(PIPELINE_ID, stages)
    
    if mode == 'enhanced':
        # 新モード: LLMサマリ + スレッド返信
        try:
            summary = generate_pipeline_summary(stage_companies)
        except Exception as e:
            logger.error(f'サマリ生成に失敗: {e}')
            # フォールバック: シンプルなサマリ
            total = sum(len(c) for c in stage_companies.values())
            summary = f"本日のパイプラインには合計 {total} 社の案件があります。詳細はスレッドをご確認ください。"
        
        if send_to_slack_with_thread(summary, stage_companies):
            logger.info('処理が正常に完了しました')
            sys.exit(0)
        else:
            logger.error('Slackへの投稿に失敗しました')
            sys.exit(1)
    else:
        # レガシーモード: Webhook
        message = format_slack_message_legacy(stage_companies)
        logger.info(f'フォーマット済みメッセージ: {len(message)} 文字')
        
        if send_to_slack_legacy(message):
            logger.info('処理が正常に完了しました')
            sys.exit(0)
        else:
            logger.error('Slackへの投稿に失敗しました')
            sys.exit(1)


if __name__ == '__main__':
    main()
