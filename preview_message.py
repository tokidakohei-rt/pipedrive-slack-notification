from main import format_slack_message

# Mock data
stage_companies = {
    'リード': {'株式会社A', '株式会社B'},
    '商談中': {'株式会社C'},
    '契約完了': set()
}

llm_report = "本日はリードステージに動きがありました。株式会社AとBが追加されています。"

# Generate message
message = format_slack_message(stage_companies, llm_report)

print(message)
