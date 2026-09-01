import os
import pandas as pd
import yfinance as yf
import requests

TRADE_LOG_FILE = "trade_log.csv"
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram_report(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload, timeout=10)

def generate_weekly_report():
    if not os.path.exists(TRADE_LOG_FILE):
        print("trade_log.csv not found.")
        return

    df = pd.read_csv(TRADE_LOG_FILE)
    signals_df = df[df['Signal'].isin(['BUY', 'SELL'])].copy()
    
    if signals_df.empty:
        print("No trades logged yet.")
        return

    gold_data = yf.download("GC=F", period="1mo", interval="5m", progress=False)
    results = []

    for _, row in signals_df.iterrows():
        try:
            entry_time = pd.to_datetime(row['Timestamp'].replace(" UTC", ""))
        except Exception:
            continue

        signal = row['Signal']
        entry = float(row['Price'])
        sl = float(row['SL'])
        tp = float(row['TP'])
        conviction = row.get('Conviction', 'STANDARD')

        future_bars = gold_data[gold_data.index >= entry_time]
        outcome = "OPEN"
        pnl = 0.0

        for _, bar in future_bars.iterrows():
            high = float(bar['High'].iloc[0] if isinstance(bar['High'], pd.Series) else bar['High'])
            low = float(bar['Low'].iloc[0] if isinstance(bar['Low'], pd.Series) else bar['Low'])

            if signal == "BUY":
                if low <= sl:
                    outcome = "LOSS"
                    pnl = sl - entry
                    break
                elif high >= tp:
                    outcome = "WIN"
                    pnl = tp - entry
                    break
            elif signal == "SELL":
                if high >= sl:
                    outcome = "LOSS"
                    pnl = entry - sl
                    break
                elif low <= tp:
                    outcome = "WIN"
                    pnl = entry - tp
                    break

        results.append({
            "Signal": signal,
            "Conviction": conviction,
            "Outcome": outcome,
            "PnL": pnl
        })

    res_df = pd.DataFrame(results)
    closed = res_df[res_df['Outcome'].isin(['WIN', 'LOSS'])]
    wins = len(closed[closed['Outcome'] == 'WIN'])
    losses = len(closed[closed['Outcome'] == 'LOSS'])
    total_closed = len(closed)
    
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
    net_pts = closed['PnL'].sum() if not closed.empty else 0.0

    report = f"""
📊 *WEEKLY PERFORMANCE REPORT (XAU/USD)*
━━━━━━━━━━━━━━━━━━━━
📈 *Total Signals:* `{len(res_df)}`
✅ *Closed Trades:* `{total_closed}` (`{wins}W` / `{losses}L`)
🎯 *Win Rate:* `{win_rate:.1f}%`
💰 *Net Points:* `{net_pts:+.2f} pts`
⏳ *Open Trades:* `{len(res_df) - total_closed}`
━━━━━━━━━━━━━━━━━━━━
_Engine: Macro + MTF Fractal System_
"""
    send_telegram_report(report)
    print("Weekly report dispatched to Telegram.")

if __name__ == "__main__":
    generate_weekly_report()
    
