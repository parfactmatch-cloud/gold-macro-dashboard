import os
import requests
import yfinance as yf
from fredapi import Fred
import pandas as pd
import csv
from datetime import datetime

FRED_API_KEY = os.getenv("FRED_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DASHBOARD_URL = "https://gold-macro-dashboard-5vuz6kxl25bappkbsjnisr7.streamlit.app/"
TV_URL = "https://www.tradingview.com/chart/?symbol=OANDA:XAUUSD"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    requests.post(url, json=payload, timeout=10)

def log_trade(direction, entry, sl, tp, lots, score):
    file_name = "trade_log.csv"
    file_exists = os.path.isfile(file_name)
    with open(file_name, mode='a', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Timestamp", "Pair", "Direction", "Entry", "SL", "TP", "Lots", "Macro_Score"])
        writer.writerow([datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), "XAUUSD", direction, entry, sl, tp, lots, score])

def fetch_cot_score():
    try:
        url = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json?cftc_contract_market_code=088691&$limit=5&$order=report_date_as_yyyy_mm_dd%20DESC"
        res = requests.get(url, timeout=10)
        df_cot = pd.DataFrame(res.json())
        net_curr = float(df_cot['noncomm_positions_long_all'].iloc[0]) - float(df_cot['noncomm_positions_short_all'].iloc[0])
        net_prev = float(df_cot['noncomm_positions_long_all'].iloc[1]) - float(df_cot['noncomm_positions_short_all'].iloc[1])
        cot_delta = net_curr - net_prev
        return (1 if cot_delta > 0 else -1), int(net_curr), int(cot_delta)
    except:
        return 0, 0, 0

def run_forward_test():
    fred = Fred(api_key=FRED_API_KEY)
    real_yield = fred.get_series('DFII10').dropna()
    ry_curr = real_yield.iloc[-1]
    ry_delta = ry_curr - real_yield.iloc[-5]

    walcl = fred.get_series('WALCL').dropna()
    tga = fred.get_series('WTREGEN').dropna()
    rrp = fred.get_series('RRPONTSYD').dropna()
    df_liq = pd.DataFrame({'W': walcl, 'T': tga, 'R': rrp}).dropna()
    net_liq = (df_liq['W'] - df_liq['T'] - df_liq['R']) / 1000000
    liq_delta = net_liq.iloc[-1] - net_liq.iloc[-2]

    gold = yf.download("GC=F", period="3mo", interval="1d", progress=False)['Close'].squeeze().dropna()
    dxy = yf.download("DX-Y.NYB", period="3mo", interval="1d", progress=False)['Close'].squeeze().dropna()
    copper = yf.download("HG=F", period="3mo", interval="1d", progress=False)['Close'].squeeze().dropna()
    combined = pd.DataFrame({'GC': gold, 'DXY': dxy, 'HG': copper}).dropna()

    dxy_delta = combined['DXY'].iloc[-1] - combined['DXY'].iloc[-5]
    returns = combined[['GC', 'DXY']].pct_change().dropna()
    rolling_corr = returns['GC'].rolling(window=30).corr(returns['DXY']).dropna().iloc[-1]
    cu_au = combined['HG'].iloc[-1] / combined['GC'].iloc[-1]

    cot_score, cot_net, cot_delta = fetch_cot_score()

    score = 0
    if ry_delta < -0.05: score += 3
    elif ry_delta > 0.05: score -= 3
    if liq_delta > 0: score += 2
    elif liq_delta < 0: score -= 2
    if dxy_delta < -0.5: score += 2
    elif dxy_delta > 0.5: score -= 2
    score += cot_score

    gold_1h = yf.download("GC=F", period="5d", interval="1h", progress=False)
    close = float(gold_1h['Close'].iloc[-1].item())
    ema50 = float(gold_1h['Close'].ewm(span=50).mean().iloc[-1].item())
    low_prev = float(gold_1h['Low'].iloc[-2].item())
    high_prev = float(gold_1h['High'].iloc[-2].item())

    risk_amount = 100.0  # 1% Risk on $10,000 baseline

    if score >= 4 and close > ema50:
        sl = round(min(low_prev, close - 4.0), 2)
        risk_per_ounce = close - sl
        lots = round(risk_amount / (risk_per_ounce * 100), 2)
        tp = round(close + (risk_per_ounce * 2), 2)

        msg = (
            f"🟢 *XAU/USD FORWARD TEST: BUY SETUP*\n\n"
            f"📊 *Macro Bias Score:* `+{score}/8 (STRONG LONG)`\n"
            f"• *10Y Real Yield:* `{ry_curr:.2f}%` ({ry_delta:+.2f}% 5D)\n"
            f"• *Net Liquidity:* `${net_liq.iloc[-1]:.2f}T` ({liq_delta:+.2f}T)\n"
            f"• *CFTC Flow:* `+{cot_delta:,} Contracts`\n"
            f"• *30D DXY Corr:* `{rolling_corr:.2f}` | *Cu/Au:* `{cu_au:.6f}`\n\n"
            f"📍 *Entry:* `${close:.2f}` (Above 1H 50 EMA)\n"
            f"🛑 *Stop Loss:* `${sl:.2f}` (Risk: ${risk_per_ounce:.2f})\n"
            f"🎯 *Take Profit:* `${tp:.2f}` (1:2 R:R)\n"
            f"⚖️ *Position Size:* `{lots} Lots` (1% Fixed Risk)\n\n"
            f"📊 [View Chart on TradingView]({TV_URL})\n"
            f"🪙 [Open Live Macro Dashboard]({DASHBOARD_URL})"
        )
        send_telegram(msg)
        log_trade("BUY", close, sl, tp, lots, score)

    elif score <= -4 and close < ema50:
        sl = round(max(high_prev, close + 4.0), 2)
        risk_per_ounce = sl - close
        lots = round(risk_amount / (risk_per_ounce * 100), 2)
        tp = round(close - (risk_per_ounce * 2), 2)

        msg = (
            f"🔴 *XAU/USD FORWARD TEST: SELL SETUP*\n\n"
            f"📊 *Macro Bias Score:* `{score}/8 (STRONG SHORT)`\n"
            f"• *10Y Real Yield:* `{ry_curr:.2f}%` ({ry_delta:+.2f}% 5D)\n"
            f"• *Net Liquidity:* `${net_liq.iloc[-1]:.2f}T` ({liq_delta:+.2f}T)\n"
            f"• *CFTC Flow:* `{cot_delta:,} Contracts`\n"
            f"• *30D DXY Corr:* `{rolling_corr:.2f}`\n\n"
            f"📍 *Entry:* `${close:.2f}` (Below 1H 50 EMA)\n"
            f"🛑 *Stop Loss:* `${sl:.2f}`\n"
            f"🎯 *Take Profit:* `${tp:.2f}` (1:2 R:R)\n"
            f"⚖️ *Position Size:* `{lots} Lots` (1% Fixed Risk)\n\n"
            f"📊 [View Chart on TradingView]({TV_URL})\n"
            f"🪙 [Open Live Macro Dashboard]({DASHBOARD_URL})"
        )
        send_telegram(msg)
        log_trade("SELL", close, sl, tp, lots, score)

if __name__ == "__main__":
    run_forward_test()
    
