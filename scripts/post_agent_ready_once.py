#!/usr/bin/env python3
"""
Fetch deals in the "agent調整完了" stage and post one entry to Slack.

Usage: ensure the following environment variables are set
    PIPEDRIVE_API_TOKEN
    PIPELINE_ID
    SLACK_WEBHOOK_URL

Optional environment variables
    AGENT_READY_STAGE_NAME (default: agent調整完了)
    OWNER_SLACK_MAP_PATH (default: config/owner_slack_map.yaml)
"""

import os
import sys
import json
import requests

PIPEDRIVE_API_BASE = 'https://api.pipedrive.com/v1'


def env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        print(f'Environment variable {name} is required.', file=sys.stderr)
        sys.exit(1)
    return value


def load_owner_map(path):
    if not os.path.exists(path):
        print(f'Owner map file not found: {path}', file=sys.stderr)
        return {}

    owner_map = {}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            key = key.strip()
            value = value.split('#', 1)[0].strip()
            if key and value:
                owner_map[key] = value
    return owner_map


def fetch_stages(pipeline_id, token):
    url = f'{PIPEDRIVE_API_BASE}/pipelines/{pipeline_id}'
    params = {'api_token': token}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get('success'):
        raise RuntimeError(f'Failed to fetch pipeline: {data}')
    stages = data.get('data', {}).get('stages', [])
    return stages


def find_stage_id(stage_name, pipeline_id, token):
    stages = fetch_stages(pipeline_id, token)
    for stage in stages:
        if stage.get('name') == stage_name:
            return stage.get('id')
    raise RuntimeError(f'Stage "{stage_name}" not found in pipeline {pipeline_id}')


def fetch_deals(pipeline_id, stage_id, token):
    url = f'{PIPEDRIVE_API_BASE}/deals'
    params = {
        'pipeline_id': pipeline_id,
        'stage_id': stage_id,
        'status': 'open',
        'limit': 500,
        'api_token': token
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get('success'):
        raise RuntimeError(f'Failed to fetch deals: {data}')
    return data.get('data') or []


def extract_owner_id(deal):
    """
    Pipedrive deals sometimes include owner info under deal['owner_id']
    or deal['user_id'] (both can be dicts). Fall back to whichever is available.
    """
    owner_obj = deal.get('owner_id') or deal.get('user_id')
    if owner_obj is None:
        return None
    if isinstance(owner_obj, dict):
        return owner_obj.get('id')
    return owner_obj


def format_owner(deal, owner_map):
    owner_id = extract_owner_id(deal)
    if owner_id is None:
        return '担当者未設定'

    slack_id = owner_map.get(str(owner_id))
    if slack_id:
        return f'<@{slack_id}>'
    return f'owner_id {owner_id}'


def post_to_slack(webhook_url, text):
    if not webhook_url:
        print('--- Slack Payload (dry run) ---')
        print(text)
        print('-------------------------------')
        return

    resp = requests.post(webhook_url, json={'text': text}, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f'Slack webhook failed: {resp.status_code} {resp.text}')


def main():
    token = env('PIPEDRIVE_API_TOKEN', required=True)
    pipeline_id = env('PIPELINE_ID', required=True)
    webhook_url = env('SLACK_WEBHOOK_URL', '')
    stage_name = env('AGENT_READY_STAGE_NAME', 'agent調整完了')
    owner_map_path = env('OWNER_SLACK_MAP_PATH', 'config/owner_slack_map.yaml')

    owner_map = load_owner_map(owner_map_path)
    stage_id = find_stage_id(stage_name, pipeline_id, token)
    deals = fetch_deals(pipeline_id, stage_id, token)

    if not deals:
        print(f'No deals found in stage "{stage_name}".')
        return

    deal = deals[0]
    title = deal.get('title') or f'Deal {deal.get("id")}'
    owner_label = format_owner(deal, owner_map)

    text = '\n'.join([
        ':rotating_light: agent調整完了ステータスの案件共有',
        f'施設名: {title}',
        f'担当: {owner_label}'
    ])

    post_to_slack(webhook_url, text)
    print(f'Posted deal "{title}" to Slack.')


if __name__ == '__main__':
    main()

