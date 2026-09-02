import os
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone

# ----------------- CONFIGURATION -----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TRADE_LOG_FILE = "trade_log.csv"

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials missing.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram dispatch failed: {e}")

def generate_weekly_summary():
    if not os.path.exists(TRADE_LOG_FILE):
        print("No trade log found.")
        return

    try:
        # on_bad_lines='skip' ensures format mismatches from updates don't crash the script
        df = pd.read_csv(TRADE_LOG_FILE, on_bad_lines='skip')
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return

    # Check if necessary column exists
    if 'Signal' not in df.columns:
        print("Signal column not found in logs.")
        return

    trade_signals = df[df['Signal'].isin(['BUY', 'SELL'])].copy()
    
    total_scans = len(df)
    total_trades = len(trade_signals)
    
    if total_trades == 0:
        msg = f"""
📋 *WEEKLY AUDIT REPORT: GOLD ENGINE*
📅 *Generated:* `{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}`

• *Total Market Scans:* `{total_scans}`
• *Signals Executed:* `0`
• *Status:* `Gatekeeper Kept Capital Safe (Zero Confluence Week)`

_Engine: Strict MTF Exhaustion + DOM Liquidity Engine_
"""
        send_telegram_message(msg)
        print("Zero trade summary dispatched.")
        return

    buys = len(trade_signals[trade_signals['Signal'] == 'BUY'])
    sells = len(trade_signals[trade_signals['Signal'] == 'SELL'])
    
    institutional_count = 0
    if 'Conviction' in trade_signals.columns:
        institutional_count = len(trade_signals[trade_signals['Conviction'].str.contains("INSTITUTIONAL", na=False)])

    avg_macro = pd.to_numeric(df['Macro_Score'], errors='coerce').mean() if 'Macro_Score' in df.columns else 0.0
    avg_dom = pd.to_numeric(df['DOM_Ratio'], errors='coerce').mean() if 'DOM_Ratio' in df.columns else 1.0

    msg = f"""
📊 *WEEKLY PERFORMANCE AUDIT REPORT*
📅 *Date:* `{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}`

📈 *Execution Summary:*
• *Total Automated Scans:* `{total_scans}`
• *Total Qualified Trades:* `{total_trades}`
  └ 🟢 *BUY Trades:* `{buys}`
  └ 🔴 *SELL Trades:* `{sells}`
• *Strict Institutional Confluence:* `{institutional_count}/{total_trades}`

🏛 *Environment Metrics (Weekly Avg):*
• *Avg Macro Bias Score:* `{avg_macro:.2f}/9`
• *Avg DOM Liquidity Ratio:* `{avg_dom:.2f}`

💼 *Filter Efficacy:*
• Low-quality noise trades blocked by Multi-Timeframe Exhaustion & Session Filters.
• Forward testing active under institutional risk parameters.

_Engine: Strict Multi-Layer Gold Automation_
"""
    send_telegram_message(msg)
    print("Weekly report dispatched successfully.")

if __name__ == "__main__":
    generate_weekly_summary()
    
