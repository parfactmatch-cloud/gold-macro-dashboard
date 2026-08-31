import os
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from fredapi import Fred

# ----------------- CONFIGURATION -----------------
FRED_API_KEY = os.getenv("FRED_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TRADE_LOG_FILE = "trade_log.csv"

# ----------------- 1. MACRO & LIQUIDITY DATA -----------------
def get_macro_score():
    score = 0
    details = {}
    
    try:
        fred = Fred(api_key=FRED_API_KEY)
        
        # 1. 10Y Real Yield (DFII10)
        tips_data = fred.get_series('DFII10')
        tips_5d_delta = tips_data.dropna().iloc[-1] - tips_data.dropna().iloc[-6]
        if tips_5d_delta < -0.05:
            score += 3
            details['TIPS'] = f"+3 (Yield Falling: {tips_5d_delta:.2f}%)"
        elif tips_5d_delta > 0.05:
            score -= 3
            details['TIPS'] = f"-3 (Yield Rising: {tips_5d_delta:.2f}%)"
        else:
            details['TIPS'] = f"0 (Yield Neutral: {tips_5d_delta:.2f}%)"
            
        # 2. Fed Net Liquidity = WALCL - TGA - RRP
        walcl = fred.get_series('WALCL').dropna().iloc[-1]
        tga = fred.get_series('WTREGEN').dropna().iloc[-1]
        rrp = fred.get_series('RRPONTSYD').dropna().iloc[-1]
        
        walcl_prev = fred.get_series('WALCL').dropna().iloc[-2]
        tga_prev = fred.get_series('WTREGEN').dropna().iloc[-2]
        rrp_prev = fred.get_series('RRPONTSYD').dropna().iloc[-2]
        
        net_liq_curr = walcl - tga - rrp
        net_liq_prev = walcl_prev - tga_prev - rrp_prev
        liq_delta = net_liq_curr - net_liq_prev
        
        if liq_delta > 0:
            score += 2
            details['Liquidity'] = "+2 (Fed Liquidity Expanding)"
        else:
            score -= 2
            details['Liquidity'] = "-2 (Fed Liquidity Contracting)"
            
    except Exception as e:
        details['Macro_Error'] = str(e)
        
    try:
        # 3. US Dollar Index (DXY)
        dxy = yf.download("DX-Y.NYB", period="10d", progress=False)
        dxy_close = dxy['Close']
        if isinstance(dxy_close, pd.DataFrame):
            dxy_close = dxy_close.iloc[:, 0]
        dxy_5d_delta = float(dxy_close.iloc[-1] - dxy_close.iloc[-6])
        
        if dxy_5d_delta < -0.50:
            score += 2
            details['DXY'] = f"+2 (DXY Weakening: {dxy_5d_delta:.2f})"
        elif dxy_5d_delta > 0.50:
            score -= 2
            details['DXY'] = f"-2 (DXY Strengthening: {dxy_5d_delta:.2f})"
        else:
            details['DXY'] = f"0 (DXY Neutral: {dxy_5d_delta:.2f})"
    except Exception as e:
        details['DXY_Error'] = str(e)
        
    return score, details

# ----------------- 2. ORDER BOOK DEPTH (DOM) -----------------
def get_order_book_score():
    score = 0
    ratio = 1.0
    try:
        url = "https://api.binance.com/api/v3/depth?symbol=PAXGUSDT&limit=20"
        res = requests.get(url, timeout=5).json()
        bids = sum([float(x[1]) for x in res['bids']])
        asks = sum([float(x[1]) for x in res['asks']])
        if asks > 0:
            ratio = bids / asks
            if ratio >= 1.25:
                score = 1
            elif ratio <= 0.75:
                score = -1
    except Exception:
        pass
    return score, ratio

# ----------------- 3. TECHNICAL & LIQUIDITY LEVELS (1H EMA & PDH/PDL) -----------------
def get_gold_technicals():
    # 1-Hour Chart for Trend Filter
    gold_1h = yf.download("GC=F", period="5d", interval="1h", progress=False)
    close_1h = gold_1h['Close']
    if isinstance(close_1h, pd.DataFrame):
        close_1h = close_1h.iloc[:, 0]
    
    current_price = float(close_1h.iloc[-1])
    ema_50 = float(close_1h.ewm(span=50, adjust=False).mean().iloc[-1])
    
    # Daily Chart for Previous Day High & Low
    gold_daily = yf.download("GC=F", period="5d", interval="1d", progress=False)
    high_daily = gold_daily['High']
    low_daily = gold_daily['Low']
    if isinstance(high_daily, pd.DataFrame):
        high_daily = high_daily.iloc[:, 0]
        low_daily = low_daily.iloc[:, 0]
        
    pdh = float(high_daily.iloc[-2])
    pdl = float(low_daily.iloc[-2])
    
    return current_price, ema_50, pdh, pdl

# ----------------- 4. TELEGRAM DISPATCH -----------------
def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram configuration missing.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload, timeout=10)

# ----------------- 5. MAIN LOGIC ENGINE -----------------
def main():
    macro_score, macro_details = get_macro_score()
    dom_score, dom_ratio = get_order_book_score()
    
    total_score = macro_score + dom_score
    current_price, ema_50, pdh, pdl = get_gold_technicals()
    
    signal = "NEUTRAL"
    tp = None
    sl = None
    
    # Volatility Spread Buffer: 25% of baseline distance ($2.50 buffer)
    spread_buffer = 2.50
    base_sl_dist = 10.0
    
    # BUY SETUP
    if total_score >= 4 and current_price > ema_50:
        # Prevent buying directly into PDH resistance
        if abs(current_price - pdh) > 2.0 or current_price > pdh:
            signal = "BUY"
            sl = round(current_price - (base_sl_dist + spread_buffer), 2)
            # Dynamic TP target anchored to PDH or 1:2 R:R
            tp = round(max(pdh, current_price + (base_sl_dist * 2) + spread_buffer), 2)
            
    # SELL SETUP
    elif total_score <= -4 and current_price < ema_50:
        # Prevent selling directly into PDL support
        if abs(current_price - pdl) > 2.0 or current_price < pdl:
            signal = "SELL"
            sl = round(current_price + (base_sl_dist + spread_buffer), 2)
            # Dynamic TP target anchored to PDL or 1:2 R:R
            tp = round(min(pdl, current_price - (base_sl_dist * 2) - spread_buffer), 2)
            
    # Record to CSV Log
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    new_record = pd.DataFrame([{
        "Timestamp": timestamp,
        "Price": current_price,
        "EMA_50": ema_50,
        "PDH": pdh,
        "PDL": pdl,
        "Macro_Score": total_score,
        "DOM_Ratio": round(dom_ratio, 2),
        "Signal": signal,
        "SL": sl,
        "TP": tp
    }])
    
    if os.path.exists(TRADE_LOG_FILE):
        new_record.to_csv(TRADE_LOG_FILE, mode='a', header=False, index=False)
    else:
        new_record.to_csv(TRADE_LOG_FILE, index=False)
        
    # Format and Dispatch Message if Signal Triggered
    if signal in ["BUY", "SELL"]:
        icon = "🟢" if signal == "BUY" else "🔴"
        msg = f"""
{icon} *INSTITUTIONAL XAU/USD ALERT: {signal}*

📊 *Macro Confluence Score:* `{total_score}/9`
📈 *Current Price:* `${current_price:.2f}`
🎯 *1H 50 EMA:* `${ema_50:.2f}`

🏛 *Key Liquidity Reference Levels:*
• *PDH (Previous Day High):* `${pdh:.2f}`
• *PDL (Previous Day Low):* `${pdl:.2f}`
• *DOM Bid/Ask Ratio:* `{dom_ratio:.2f}`

⚡ *Execution Plan:*
• *Entry:* `${current_price:.2f}`
• *Stop Loss:* `${sl:.2f}` (Spread Buffered)
• *Take Profit:* `${tp:.2f}` (Target Level)

_Engine: Automated Macro & Order Flow System_
"""
        send_telegram_alert(msg)
        print(f"Triggered {signal} alert at {current_price}")
    else:
        print(f"Scan complete. Neutral conditions (Score: {total_score}, Price: {current_price}, 50 EMA: {ema_50})")

if __name__ == "__main__":
    main()
    
