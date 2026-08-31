import os
import requests
import yfinance as yf
from fredapi import Fred
import pandas as pd
import numpy as np
import csv
from datetime import datetime

# --- CONFIGURATION & SECRETS ---
FRED_API_KEY = os.getenv("FRED_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DASHBOARD_URL = "https://gold-macro-dashboard-5vuz6kxl25bappkbsjnisr7.streamlit.app/"
TV_URL = "https://in.tradingview.com/chart/?symbol=OANDA:XAUUSD"

def safe_iloc(series, idx, default=0.0):
    try:
        if len(series) >= abs(idx):
            return float(series.iloc[idx])
        elif len(series) > 0:
            return float(series.iloc[-1])
        return float(default)
    except:
        return float(default)

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        res = requests.post(url, json=payload, timeout=10)
        print(f"Telegram API Status Code: {res.status_code}")
    except Exception as e:
        print(f"Telegram Send Error: {e}")

def log_trade(direction, entry, sl, tp, lots, score, dom_ratio=1.0):
    file_name = "trade_log.csv"
    file_exists = os.path.isfile(file_name)
    with open(file_name, mode='a', newline='') as file:
        writer = csv.writer(file)
        if not file_exists:
            writer.writerow(["Timestamp", "Pair", "Direction", "Entry", "SL", "TP", "Lots", "Macro_Score", "DOM_Ratio"])
        writer.writerow([datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), "XAUUSD", direction, entry, sl, tp, lots, score, dom_ratio])

def fetch_free_dom_imbalance():
    """100% Free Public Order Book Depth Filter (Top 20 Levels)"""
    try:
        url = "https://api.binance.com/api/v3/depth?symbol=PAXGUSDT&limit=20"
        res = requests.get(url, timeout=5)
        data = res.json()
        
        bids = [float(item[1]) for item in data.get('bids', [])]
        asks = [float(item[1]) for item in data.get('asks', [])]
        
        total_bids = sum(bids)
        total_asks = sum(asks)
        
        if total_asks > 0:
            ratio = round(total_bids / total_asks, 2)
        else:
            ratio = 1.0
            
        score = 0
        if ratio >= 1.25:
            score = 1   # Heavy Buyer DOM Imbalance (+1 Point)
        elif ratio <= 0.75:
            score = -1  # Heavy Seller DOM Imbalance (-1 Point)
            
        return score, ratio, total_bids, total_asks
    except Exception as e:
        print(f"DOM Fetch Notice: {e}")
        return 0, 1.0, 0.0, 0.0

def fetch_cot_score():
    try:
        url = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json?cftc_contract_market_code=088691&$limit=5&$order=report_date_as_yyyy_mm_dd%20DESC"
        res = requests.get(url, timeout=10)
        data = res.json()
        if isinstance(data, list) and len(data) >= 2:
            df_cot = pd.DataFrame(data)
            net_curr = float(df_cot['noncomm_positions_long_all'].iloc[0]) - float(df_cot['noncomm_positions_short_all'].iloc[0])
            net_prev = float(df_cot['noncomm_positions_long_all'].iloc[1]) - float(df_cot['noncomm_positions_short_all'].iloc[1])
            cot_delta = net_curr - net_prev
            return (1 if cot_delta > 0 else -1), int(net_curr), int(cot_delta)
        return 0, 0, 0
    except Exception as e:
        print(f"COT Fetch Notice: {e}")
        return 0, 0, 0

def run_forward_test():
    print("=== STARTING INSTITUTIONAL MACRO & ORDER FLOW EVALUATION ===")
    
    # 1. FRED Macro Data
    fred = Fred(api_key=FRED_API_KEY)
    real_yield = fred.get_series('DFII10').dropna()
    ry_curr = safe_iloc(real_yield, -1, 2.0)
    ry_prev = safe_iloc(real_yield, -5, ry_curr)
    ry_delta = ry_curr - ry_prev

    walcl = fred.get_series('WALCL').dropna()
    tga = fred.get_series('WTREGEN').dropna()
    rrp = fred.get_series('RRPONTSYD').dropna()
    df_liq = pd.DataFrame({'W': walcl, 'T': tga, 'R': rrp}).dropna()
    
    if len(df_liq) >= 2:
        net_liq = (df_liq['W'] - df_liq['T'] - df_liq['R']) / 1000000
        liq_curr = safe_iloc(net_liq, -1, 5.8)
        liq_delta = liq_curr - safe_iloc(net_liq, -2, liq_curr)
    else:
        liq_curr, liq_delta = 5.8, 0.0

    # 2. Intermarket Correlations & Ratios
    gold = yf.download("GC=F", period="3mo", interval="1d", progress=False)['Close'].squeeze().dropna()
    dxy = yf.download("DX-Y.NYB", period="3mo", interval="1d", progress=False)['Close'].squeeze().dropna()
    copper = yf.download("HG=F", period="3mo", interval="1d", progress=False)['Close'].squeeze().dropna()
    combined = pd.DataFrame({'GC': gold, 'DXY': dxy, 'HG': copper}).dropna()

    dxy_curr = safe_iloc(combined['DXY'], -1, 100.0)
    dxy_delta = dxy_curr - safe_iloc(combined['DXY'], -5, dxy_curr)
    returns = combined[['GC', 'DXY']].pct_change().dropna()
    rolling_corr = returns['GC'].rolling(window=30).corr(returns['DXY']).dropna()
    corr_val = safe_iloc(rolling_corr, -1, -0.40)
    
    gold_close_daily = safe_iloc(combined['GC'], -1, 4600.0)
    cu_au = safe_iloc(combined['HG'], -1, 4.0) / (gold_close_daily if gold_close_daily else 1.0)

    # 3. Positioning & Microstructure Data
    cot_score, cot_net, cot_delta = fetch_cot_score()
    dom_score, dom_ratio, bids_vol, asks_vol = fetch_free_dom_imbalance()

    # 4. Total Macro + DOM Bias Score Calculation
    score = 0
    if ry_delta < -0.05: score += 3
    elif ry_delta > 0.05: score -= 3
    
    if liq_delta > 0: score += 2
    elif liq_delta < 0: score -= 2
    
    if dxy_delta < -0.5: score += 2
    elif dxy_delta > 0.5: score -= 2
    
    score += cot_score
    score += dom_score  # Dynamic DOM Weight

    # 5. Technical 1H Execution Structure
    gold_1h = yf.download("GC=F", period="5d", interval="1h", progress=False)
    close = float(safe_iloc(gold_1h['Close'], -1, 0.0))
    ema50 = float(safe_iloc(gold_1h['Close'].ewm(span=50).mean(), -1, 0.0))
    low_prev = float(safe_iloc(gold_1h['Low'], -2, close - 4.0))
    high_prev = float(safe_iloc(gold_1h['High'], -2, close + 4.0))

    risk_amount = 100.0  # 1% Risk on $10,000 baseline

    # Terminal Log Output
    print(f"Macro + DOM Score: {score}/9")
    print(f"10Y Real Yield: {ry_curr:.2f}% (5D Delta: {ry_delta:+.2f}%)")
    print(f"Fed Net Liquidity: ${liq_curr:.2f}T (Delta: {liq_delta:+.2f}T)")
    print(f"DXY 5D Delta: {dxy_delta:+.2f} | 30D Corr: {corr_val:.2f}")
    print(f"CFTC Delta: {cot_delta:+,} Contracts (Score: {cot_score:+})")
    print(f"DOM Imbalance: {dom_ratio}x (Score: {dom_score:+})")
    print(f"1H Close: ${close:.2f} | 1H 50 EMA: ${ema50:.2f}")

    # BUY TRIGGER (Macro + DOM + 25/75 Spread Logic)
    if score >= 4 and close > ema50:
        base_risk = close - min(low_prev, close - 4.0)
        spread_buffer = round(base_risk * 0.25, 2)
        sl = round(close - (base_risk + spread_buffer), 2)
        total_risk = close - sl
        lots = round(risk_amount / (total_risk * 100), 2)
        tp = round(close + (base_risk * 2) + spread_buffer, 2)

        msg = (
            f"🟢 *XAU/USD FORWARD TEST: BUY SETUP*\n\n"
            f"📊 *Macro Bias Score:* `+{score}/9 (STRONG LONG)`\n"
            f"• *10Y Real Yield:* `{ry_curr:.2f}%` ({ry_delta:+.2f}% 5D)\n"
            f"• *Net Liquidity:* `${liq_curr:.2f}T` ({liq_delta:+.2f}T)\n"
            f"• *CFTC Flow:* `+{cot_delta:,} Contracts`\n"
            f"• *DOM Imbalance:* `{dom_ratio}x Buyers` (Order Flow Bullish)\n"
            f"• *30D DXY Corr:* `{corr_val:.2f}` | *Cu/Au:* `{cu_au:.6f}`\n\n"
            f"📍 *Entry:* `${close:.2f}` (Above 1H 50 EMA)\n"
            f"🛡️ *Spread Buffer (25%):* `${spread_buffer:.2f}`\n"
            f"🛑 *Stop Loss:* `${sl:.2f}` (Risk: ${total_risk:.2f})\n"
            f"🎯 *Take Profit:* `${tp:.2f}` (1:2 Net R:R)\n"
            f"⚖️ *Position Size:* `{lots} Lots` (1% Fixed Risk)\n\n"
            f"📊 [View Chart on TradingView]({TV_URL})\n"
            f"🪙 [Open Live Macro Dashboard]({DASHBOARD_URL})"
        )
        send_telegram(msg)
        log_trade("BUY", close, sl, tp, lots, score, dom_ratio)
        print(">>> SUCCESS: Buy alert sent to Telegram.")

    # SELL TRIGGER (Macro + DOM + 25/75 Spread Logic)
    elif score <= -4 and close < ema50:
        base_risk = max(high_prev, close + 4.0) - close
        spread_buffer = round(base_risk * 0.25, 2)
        sl = round(close + base_risk + spread_buffer, 2)
        total_risk = sl - close
        lots = round(risk_amount / (total_risk * 100), 2)
        tp = round(close - (base_risk * 2) - spread_buffer, 2)

        msg = (
            f"🔴 *XAU/USD FORWARD TEST: SELL SETUP*\n\n"
            f"📊 *Macro Bias Score:* `{score}/9 (STRONG SHORT)`\n"
            f"• *10Y Real Yield:* `{ry_curr:.2f}%` ({ry_delta:+.2f}% 5D)\n"
            f"• *Net Liquidity:* `${liq_curr:.2f}T` ({liq_delta:+.2f}T)\n"
            f"• *CFTC Flow:* `{cot_delta:,} Contracts`\n"
            f"• *DOM Imbalance:* `{dom_ratio}x Sellers` (Order Flow Bearish)\n"
            f"• *30D DXY Corr:* `{corr_val:.2f}`\n\n"
            f"📍 *Entry:* `${close:.2f}` (Below 1H 50 EMA)\n"
            f"🛡️ *Spread Buffer (25%):* `${spread_buffer:.2f}`\n"
            f"🛑 *Stop Loss:* `${sl:.2f}` (Risk: ${total_risk:.2f})\n"
            f"🎯 *Take Profit:* `${tp:.2f}` (1:2 Net R:R)\n"
            f"⚖️ *Position Size:* `{lots} Lots` (1% Fixed Risk)\n\n"
            f"📊 [View Chart on TradingView]({TV_URL})\n"
            f"🪙 [Open Live Macro Dashboard]({DASHBOARD_URL})"
        )
        send_telegram(msg)
        log_trade("SELL", close, sl, tp, lots, score, dom_ratio)
        print(">>> SUCCESS: Sell alert sent to Telegram.")

    else:
        print(">>> STATUS: Market is in Neutral/Filter Zone. No alert triggered.")

if __name__ == "__main__":
    run_forward_test()
            
