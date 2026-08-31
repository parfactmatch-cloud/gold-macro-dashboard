import os
import requests
import pandas as pd
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    requests.post(url, json=payload, timeout=10)

def generate_weekly_summary():
    file_name = "trade_log.csv"
    if not os.path.exists(file_name):
        send_telegram("📊 *WEEKLY PERFORMANCE REPORT*\n\nNo trades logged this week.")
        return

    df = pd.read_csv(file_name)
    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
    one_week_ago = datetime.utcnow() - timedelta(days=7)
    weekly_trades = df[df['Timestamp'] >= one_week_ago]

    total_trades = len(weekly_trades)
    if total_trades == 0:
        send_telegram("📊 *WEEKLY MACRO ENGINE REPORT*\n\nStatus: Market remained in neutral/filter zone. 0 trades taken this week.")
        return

    buys = len(weekly_trades[weekly_trades['Direction'] == 'BUY'])
    sells = len(weekly_trades[weekly_trades['Direction'] == 'SELL'])

    msg = (
        f"📊 *WEEKLY FORWARD TEST SUMMARY*\n"
        f"🗓️ *Period:* Past 7 Days\n\n"
        f"• *Total Executions:* `{total_trades}`\n"
        f"• *Long Setups:* `{buys}`\n"
        f"• *Short Setups:* `{sells}`\n"
        f"• *Fixed Risk Model:* `1% Per Trade`\n\n"
        f"🪙 [View Live Macro Dashboard](https://gold-macro-dashboard-5vuz6kxl25bappkbsjnisr7.streamlit.app/)"
    )
    send_telegram(msg)

if __name__ == "__main__":
    generate_weekly_summary()
  
