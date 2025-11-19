#!/usr/bin/env python3
"""
Pipedrive パイプライン自動レポート Slack 通知スクリプト

指定されたパイプラインの全ステージの案件（企業）一覧を取得し、
ステージごとにまとめてSlackに投稿する。
"""

import os
import sys
import logging
from typing import Dict, List, Set
import requests

# ログ設定
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 環境変数から設定を取得
PIPEDRIVE_API_TOKEN = os.getenv('PIPEDRIVE_API_TOKEN')
PIPELINE_ID = os.getenv('PIPELINE_ID')
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
    if not SLACK_WEBHOOK_URL:
        logger.error('SLACK_WEBHOOK_URL が設定されていません')
        sys.exit(1)


def get_pipeline_stages(pipeline_id: str) -> List[Dict]:
    """
    パイプラインのステージ一覧を取得
    
    Args:
        pipeline_id: パイプラインID
        
    Returns:
        ステージ情報のリスト
    """
    url = f'{PIPEDRIVE_API_BASE}/pipelines/{pipeline_id}/stages'
    params = {'api_token': PIPEDRIVE_API_TOKEN}
    
    try:
        logger.info(f'パイプラインのステージ情報を取得中: pipeline_id={pipeline_id}')
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        logger.debug(f'Pipedrive API レスポンス: {data}')
        
        if not data.get('success'):
            error_msg = data.get('error', 'Unknown error')
            logger.error(f'Pipedrive API エラー: {error_msg}')
            logger.error(f'レスポンス全体: {data}')
            sys.exit(1)
        
        stages = data.get('data', [])
        
        if not stages:
            logger.warning(f'パイプライン {pipeline_id} にステージが見つかりませんでした')
            logger.warning(f'レスポンス全体: {data}')
            # 空のリストを返す（エラーではなく警告として扱う）
            return []
        
        # ステージをorder_nrでソート
        stages_sorted = sorted(stages, key=lambda x: x.get('order_nr', 0))
        
        logger.info(f'パイプライン {pipeline_id} から {len(stages_sorted)} 個のステージを取得')
        for i, stage in enumerate(stages_sorted, 1):
            logger.info(f'  ステージ {i}: id={stage.get("id")}, name={stage.get("name")}, order_nr={stage.get("order_nr")}')
        
        return stages_sorted
        
    except requests.exceptions.RequestException as e:
        logger.error(f'パイプラインのステージ情報の取得に失敗: {e}')
        if hasattr(e, 'response') and e.response is not None:
            logger.error(f'レスポンスステータス: {e.response.status_code}')
            logger.error(f'レスポンスボディ: {e.response.text}')
        sys.exit(1)


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
            logger.error(f'Pipedrive API エラー: {data.get("error", "Unknown error")}')
            return []
        
        deals = data.get('data', [])
        logger.info(f'ステージ {stage_id} から {len(deals)} 件のDealを取得')
        return deals
        
    except requests.exceptions.RequestException as e:
        logger.error(f'Deal情報の取得に失敗 (stage_id: {stage_id}): {e}')
        return []


def group_companies_by_stage(pipeline_id: str, stages: List[Dict]) -> Dict[str, Set[str]]:
    """
    ステージごとに企業名をグルーピング
    
    Args:
        pipeline_id: パイプラインID
        stages: ステージ情報のリスト
        
    Returns:
        ステージ名をキー、企業名のセットを値とする辞書
    """
    stage_companies: Dict[str, Set[str]] = {}
    
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
        
        stage_companies[stage_name] = companies
        logger.info(f'ステージ "{stage_name}": {len(companies)} 社')
        if companies:
            logger.debug(f'  企業名: {sorted(companies)}')
    
    return stage_companies


def format_slack_message(stage_companies: Dict[str, Set[str]]) -> str:
    """
    Slackメッセージをフォーマット
    
    Args:
        stage_companies: ステージ名をキー、企業名のセットを値とする辞書
        
    Returns:
        フォーマット済みメッセージ
    """
    message_parts = ['本日のPipedriveパイプライン状況\n']
    
    for stage_name, companies in stage_companies.items():
        message_parts.append(f'【ステージ: {stage_name}】')
        
        if companies:
            # 企業名をソートして表示
            sorted_companies = sorted(companies)
            for company in sorted_companies:
                message_parts.append(f'・{company}')
        else:
            message_parts.append('・該当なし')
        
        message_parts.append('')  # 空行
    
    return '\n'.join(message_parts)


def send_to_slack(message: str) -> bool:
    """
    Slack Incoming Webhookにメッセージを送信
    
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
    validate_env_vars()
    
    # パイプラインのステージ一覧を取得
    stages = get_pipeline_stages(PIPELINE_ID)
    
    if not stages:
        logger.error('ステージが見つかりませんでした。以下を確認してください:')
        logger.error('1. PIPELINE_IDが正しいか確認')
        logger.error('2. パイプラインにステージが設定されているか確認')
        logger.error('3. APIトークンにパイプラインへのアクセス権限があるか確認')
        # ステージがない場合でもSlackに通知する（空のメッセージ）
        message = '本日のPipedriveパイプライン状況\n\nステージが見つかりませんでした。パイプラインIDとアクセス権限を確認してください。'
        send_to_slack(message)
        sys.exit(1)
    
    # ステージごとに企業名をグルーピング
    stage_companies = group_companies_by_stage(PIPELINE_ID, stages)
    
    # Slackメッセージをフォーマット
    message = format_slack_message(stage_companies)
    logger.info(f'フォーマット済みメッセージ: {len(message)} 文字')
    
    # Slackに投稿
    if send_to_slack(message):
        logger.info('処理が正常に完了しました')
        sys.exit(0)
    else:
        logger.error('Slackへの投稿に失敗しました')
        sys.exit(1)


if __name__ == '__main__':
    main()

