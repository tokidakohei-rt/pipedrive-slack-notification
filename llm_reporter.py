"""
LLM Reporting Module using Google Gemini
"""
import os
import logging
from typing import Dict, Set
import google.generativeai as genai

logger = logging.getLogger(__name__)

def generate_report(stage_companies: Dict[str, Set[str]]) -> str:
    """
    Generates a summary report using Google Gemini based on the pipeline data.

    Args:
        stage_companies: Dictionary mapping stage names to sets of company names.

    Returns:
        A string containing the LLM-generated analysis.
    """
    api_key = os.getenv('GOOGLE_API_KEY')
    if not api_key:
        logger.warning("GOOGLE_API_KEY not found. Skipping LLM report generation.")
        return "（LLMレポート機能は無効化されています: GOOGLE_API_KEYが設定されていません）"

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')

        # Construct the prompt
        prompt = "あなたは優秀なセールスマネージャーのアシスタントです。\n"
        prompt += "以下のPipedriveのパイプライン状況（ステージごとの案件リスト）をもとに、\n"
        prompt += "現状の分析、注目すべき点、またはチームへの簡単なアドバイスを含む短いレポートを作成してください。\n"
        prompt += "レポートは簡潔に、Slackで読みやすい形式（箇条書きなど）で出力してください。\n\n"
        prompt += "【パイプライン状況】\n"

        has_deals = False
        for stage, companies in stage_companies.items():
            prompt += f"- {stage}: {len(companies)}件\n"
            if companies:
                has_deals = True
                # Limit the number of companies listed to avoid token limits if necessary, 
                # but for now list all as they are usually just names.
                prompt += f"  (案件: {', '.join(sorted(companies))})\n"
        
        if not has_deals:
            return "本日は案件がありません。新規開拓に注力しましょう！"

        logger.info("Generating report with Gemini...")
        response = model.generate_content(prompt)
        
        logger.info("Gemini report generated successfully.")
        return response.text

    except Exception as e:
        logger.error(f"Failed to generate LLM report: {e}")
        return "（レポート生成中にエラーが発生しました）"
