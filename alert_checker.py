#!/usr/bin/env python3
"""
Pipedrive 案件アラートチェッカー

期限切れ間近と滞留案件を検知し、Slackに通知する。
- 期限切れ間近: 引き渡し希望日の3日前、1日前、当日
- 滞留アラート: ステージ滞留3日、7日、14日、30日
"""

import os
import sys
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
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
SLACK_BOT_TOKEN = os.getenv('SLACK_BOT_TOKEN')
SLACK_CHANNEL = os.getenv('SLACK_CHANNEL')
HANDOVER_DATE_FIELD_KEY = os.getenv('HANDOVER_DATE_FIELD_KEY', 'b459bec642f11294904272a4fe6273d3591b9566')
SLACK_THREAD_TS_FIELD_KEY = os.getenv('SLACK_THREAD_TS_FIELD_KEY')

# Pipedrive API設定
PIPEDRIVE_API_BASE = 'https://api.pipedrive.com/v1'

# アラート設定
DEADLINE_ALERT_DAYS = [3, 1, 0]  # 3日前、1日前、当日
STAGNATION_ALERT_DAYS = [3, 7, 14, 30]  # 3日、7日、14日、30日


def validate_env_vars():
    """環境変数の検証"""
    if not PIPEDRIVE_API_TOKEN:
        logger.error('PIPEDRIVE_API_TOKEN が設定されていません')
        sys.exit(1)
    if not PIPELINE_ID:
        logger.error('PIPELINE_ID が設定されていません')
        sys.exit(1)
    if not SLACK_BOT_TOKEN:
        logger.error('SLACK_BOT_TOKEN が設定されていません')
        sys.exit(1)
    if not SLACK_CHANNEL:
        logger.error('SLACK_CHANNEL が設定されていません')
        sys.exit(1)


def get_all_open_deals(pipeline_id: str) -> List[Dict]:
    """
    指定パイプラインの全オープン案件を取得

    Args:
        pipeline_id: パイプラインID

    Returns:
        Deal情報のリスト
    """
    url = f'{PIPEDRIVE_API_BASE}/deals'
    params = {
        'api_token': PIPEDRIVE_API_TOKEN,
        'pipeline_id': pipeline_id,
        'status': 'open',
        'limit': 500
    }

    try:
        logger.info(f'パイプライン {pipeline_id} の全案件を取得中...')
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()

        if not data.get('success'):
            error_msg = data.get('error', 'Unknown error')
            logger.error(f'Pipedrive API エラー: {error_msg}')
            return []

        deals = data.get('data') or []
        logger.info(f'{len(deals)} 件の案件を取得しました')
        return deals

    except requests.exceptions.RequestException as e:
        logger.error(f'案件取得に失敗: {e}')
        return []


def extract_custom_field(deal: Dict, field_key: str) -> Optional[str]:
    """
    Dealからカスタムフィールドの値を抽出

    Args:
        deal: Deal情報
        field_key: カスタムフィールドのキー

    Returns:
        フィールド値（文字列）またはNone
    """
    if not field_key or not deal:
        return None

    # 直接フィールドとして存在する場合
    if field_key in deal and deal[field_key]:
        value = deal[field_key]
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and 'value' in value:
            return str(value['value'])

    return None


def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """
    日付文字列をdatetimeオブジェクトに変換

    Args:
        date_str: 日付文字列（YYYY-MM-DD形式）

    Returns:
        datetimeオブジェクトまたはNone
    """
    if not date_str:
        return None

    try:
        # YYYY-MM-DD形式をパース
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        logger.debug(f'日付のパースに失敗: {date_str}')
        return None


def check_deadline_alerts(deals: List[Dict]) -> List[Dict]:
    """
    期限切れ間近の案件をチェック

    Args:
        deals: 全案件のリスト

    Returns:
        アラート対象の案件リスト（各案件に'alert_type'と'days_until'を追加）
    """
    alerts = []
    today = datetime.now().date()

    for deal in deals:
        handover_date_str = extract_custom_field(deal, HANDOVER_DATE_FIELD_KEY)
        if not handover_date_str:
            continue

        handover_date = parse_date(handover_date_str)
        if not handover_date:
            continue

        handover_date = handover_date.date()
        days_until = (handover_date - today).days

        # 期限を過ぎている、または該当する日数前の場合にアラート
        if days_until in DEADLINE_ALERT_DAYS or days_until < 0:
            alert_deal = deal.copy()
            alert_deal['alert_type'] = 'deadline'
            alert_deal['days_until'] = days_until
            alert_deal['handover_date'] = handover_date.strftime('%Y-%m-%d')
            alerts.append(alert_deal)
            logger.info(f'期限アラート: {deal.get("title")} (残り{days_until}日)')

    return alerts


def check_stagnation_alerts(deals: List[Dict]) -> List[Dict]:
    """
    滞留案件をチェック

    Args:
        deals: 全案件のリスト

    Returns:
        アラート対象の案件リスト（各案件に'alert_type'と'stagnation_days'を追加）
    """
    alerts = []
    now = datetime.now(timezone.utc)

    for deal in deals:
        stage_change_time_str = deal.get('stage_change_time')
        if not stage_change_time_str:
            continue

        try:
            # ISO 8601形式をパース（タイムゾーン対応）
            stage_change_time = datetime.fromisoformat(stage_change_time_str.replace('Z', '+00:00'))
            days_in_stage = (now - stage_change_time).days

            # 滞留日数が該当する場合にアラート（ちょうどその日数の場合のみ）
            if days_in_stage in STAGNATION_ALERT_DAYS:
                alert_deal = deal.copy()
                alert_deal['alert_type'] = 'stagnation'
                alert_deal['stagnation_days'] = days_in_stage
                alerts.append(alert_deal)
                logger.info(f'滞留アラート: {deal.get("title")} ({days_in_stage}日間)')
        except (ValueError, AttributeError) as e:
            logger.debug(f'stage_change_timeのパースに失敗: {stage_change_time_str}, エラー: {e}')
            continue

    return alerts


def get_stage_name(stage_id: int) -> str:
    """
    ステージIDからステージ名を取得

    Args:
        stage_id: ステージID

    Returns:
        ステージ名
    """
    url = f'{PIPEDRIVE_API_BASE}/stages/{stage_id}'
    params = {'api_token': PIPEDRIVE_API_TOKEN}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get('success') and data.get('data'):
            return data['data'].get('name', f'ステージ{stage_id}')
    except Exception as e:
        logger.debug(f'ステージ名取得エラー: {e}')

    return f'ステージ{stage_id}'


def format_deadline_alert_message(deal: Dict) -> str:
    """
    期限アラートメッセージをフォーマット

    Args:
        deal: 案件情報

    Returns:
        フォーマット済みメッセージ
    """
    title = deal.get('title', '不明')
    days_until = deal.get('days_until', 0)
    handover_date = deal.get('handover_date', '不明')
    stage_id = deal.get('stage_id')
    stage_name = get_stage_name(stage_id) if stage_id else '不明'

    if days_until < 0:
        urgency = '🚨'
        status = f'期限超過（{abs(days_until)}日経過）'
    elif days_until == 0:
        urgency = '⚠️'
        status = '本日が期限'
    elif days_until == 1:
        urgency = '⚠️'
        status = '明日が期限'
    else:
        urgency = '📅'
        status = f'{days_until}日後が期限'

    message = f"""{urgency} *期限アラート: {status}*

企業名: {title}
引き渡し希望日: {handover_date}
現在のステージ: {stage_name}

対応をご確認ください。"""

    return message


def format_stagnation_alert_message(deal: Dict) -> str:
    """
    滞留アラートメッセージをフォーマット

    Args:
        deal: 案件情報

    Returns:
        フォーマット済みメッセージ
    """
    title = deal.get('title', '不明')
    stagnation_days = deal.get('stagnation_days', 0)
    stage_id = deal.get('stage_id')
    stage_name = get_stage_name(stage_id) if stage_id else '不明'

    if stagnation_days >= 30:
        urgency = '🚨'
    elif stagnation_days >= 14:
        urgency = '⚠️'
    else:
        urgency = '📌'

    message = f"""{urgency} *滞留アラート: {stagnation_days}日間同じステージ*

企業名: {title}
現在のステージ: {stage_name}
滞留期間: {stagnation_days}日間

次のアクションをご検討ください。"""

    return message


def post_slack_message(text: str, thread_ts: Optional[str] = None) -> bool:
    """
    Slackにメッセージを投稿

    Args:
        text: 投稿するメッセージ
        thread_ts: スレッドTS（スレッドに返信する場合）

    Returns:
        送信成功時True
    """
    headers = {
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}',
        'Content-Type': 'application/json'
    }

    payload = {
        'channel': SLACK_CHANNEL,
        'text': text,
        'unfurl_links': False,
        'unfurl_media': False
    }

    if thread_ts:
        payload['thread_ts'] = thread_ts

    try:
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

        logger.info(f'Slack投稿成功: thread_ts={thread_ts or "new"}')
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f'Slack投稿に失敗: {e}')
        return False


def send_alert(deal: Dict):
    """
    アラートをSlackに送信

    Args:
        deal: アラート対象の案件
    """
    alert_type = deal.get('alert_type')

    # メッセージをフォーマット
    if alert_type == 'deadline':
        message = format_deadline_alert_message(deal)
    elif alert_type == 'stagnation':
        message = format_stagnation_alert_message(deal)
    else:
        logger.warning(f'不明なアラートタイプ: {alert_type}')
        return

    # スレッドTSを取得（存在すればスレッドに投稿）
    thread_ts = None
    if SLACK_THREAD_TS_FIELD_KEY:
        thread_ts = extract_custom_field(deal, SLACK_THREAD_TS_FIELD_KEY)

    if thread_ts:
        logger.info(f'案件 {deal.get("title")} のスレッド {thread_ts} に投稿')
    else:
        logger.info(f'案件 {deal.get("title")} をチャンネルに投稿（スレッドなし）')

    # Slackに投稿
    post_slack_message(message, thread_ts)


def main():
    """メイン処理"""
    logger.info('Pipedrive アラートチェッカーを開始')

    # 環境変数の検証
    validate_env_vars()

    # 全案件を取得
    deals = get_all_open_deals(PIPELINE_ID)

    if not deals:
        logger.info('案件が見つかりませんでした')
        return

    # 期限切れ間近アラートをチェック
    deadline_alerts = check_deadline_alerts(deals)
    logger.info(f'期限アラート: {len(deadline_alerts)} 件')

    # 滞留アラートをチェック
    stagnation_alerts = check_stagnation_alerts(deals)
    logger.info(f'滞留アラート: {len(stagnation_alerts)} 件')

    # アラートを送信
    all_alerts = deadline_alerts + stagnation_alerts

    if not all_alerts:
        logger.info('アラート対象の案件はありませんでした')
        return

    logger.info(f'合計 {len(all_alerts)} 件のアラートを送信します')

    for alert in all_alerts:
        send_alert(alert)

    logger.info('アラートチェッカーが完了しました')


if __name__ == '__main__':
    main()
