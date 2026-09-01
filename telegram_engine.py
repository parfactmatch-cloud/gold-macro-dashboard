import os
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from fredapi import Fred

# ----------------- CONFIGURATION -----------------
FRED_API_KEY = os.getenv("FRED_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TRADE_LOG_FILE = "trade_log.csv"

# ----------------- 1. MACRO & LIQUIDITY ENGINE -----------------
def get_macro_score():
    score = 0
    details = {}
    
    try:
        fred = Fred(api_key=FRED_API_KEY)
        
        # 10Y TIPS Real Yield (DFII10)
        tips_data = fred.get_series('DFII10')
        tips_clean = tips_data.dropna()
        tips_5d_delta = tips_clean.iloc[-1] - tips_clean.iloc[-6]
        if tips_5d_delta < -0.05:
            score += 3
            details['TIPS'] = f"+3 (Yield Falling: {tips_5d_delta:.2f}%)"
        elif tips_5d_delta > 0.05:
            score -= 3
            details['TIPS'] = f"-3 (Yield Rising: {tips_5d_delta:.2f}%)"
        else:
            details['TIPS'] = f"0 (Yield Neutral: {tips_5d_delta:.2f}%)"
            
        # Fed Net Liquidity = WALCL - WTREGEN (TGA) - RRPONTSYD (RRP)
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
        # US Dollar Index (DXY)
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

# ----------------- 3. MULTI-TIMEFRAME FRACTAL LIMIT ENGINE (1H, 30M, 15M, 5M) -----------------
def get_mtf_fractal_confluence(current_price):
    confluence_found = False
    confluence_type = "NONE"
    levels = {}
    
    try:
        # 1H & 30M Macro Anchors (50% Equilibrium)
        gold_1h = yf.download("GC=F", period="5d", interval="1h", progress=False)
        gold_30m = yf.download("GC=F", period="5d", interval="30m", progress=False)
        
        h_1h = float(np.max(gold_1h['High'].values.flatten()[-24:]))
        l_1h = float(np.min(gold_1h['Low'].values.flatten()[-24:]))
        level_1h_50 = round((h_1h + l_1h) / 2, 2)
        
        h_30m = float(np.max(gold_30m['High'].values.flatten()[-16:]))
        l_30m = float(np.min(gold_30m['Low'].values.flatten()[-16:]))
        level_30m_50 = round((h_30m + l_30m) / 2, 2)
        
        levels['1H_50'] = level_1h_50
        levels['30M_50'] = level_30m_50
        
        # 15M Structural Swing 4X Projection
        gold_15m = yf.download("GC=F", period="3d", interval="15m", progress=False)
        h_15m = gold_15m['High'].values.flatten()
        l_15m = gold_15m['Low'].values.flatten()
        range_15m = max(float(abs(h_15m[-4] - l_15m[-4])), 2.0)
        
        exhaust_15m_4x_down = round(h_1h - (range_15m * 4), 2)
        exhaust_15m_4x_up = round(l_1h + (range_15m * 4), 2)
        levels['15M_4X_Down'] = exhaust_15m_4x_down
        levels['15M_4X_Up'] = exhaust_15m_4x_up
        
        # 5M Micro Swing 4X Projection
        gold_5m = yf.download("GC=F", period="1d", interval="5m", progress=False)
        h_5m = gold_5m['High'].values.flatten()
        l_5m = gold_5m['Low'].values.flatten()
        range_5m = max(float(abs(h_5m[-6] - l_5m[-6])), 1.0)
        
        exhaust_5m_4x_down = round(h_1h - (range_5m * 4), 2)
        exhaust_5m_4x_up = round(l_1h + (range_5m * 4), 2)
        levels['5M_4X_Down'] = exhaust_5m_4x_down
        levels['5M_4X_Up'] = exhaust_5m_4x_up
        
        # Bullish Fractal Confluence:
        # Macro Equilibrium (1H/30M 50%) aligns with 15M or 5M 4X Downward Exhaustion
        if (abs(level_1h_50 - exhaust_15m_4x_down) <= 3.5 or abs(level_1h_50 - exhaust_5m_4x_down) <= 2.5 or abs(level_30m_50 - exhaust_5m_4x_down) <= 2.0):
            if abs(current_price - level_1h_50) <= 3.0 or abs(current_price - level_30m_50) <= 2.0:
                confluence_found = True
                confluence_type = "BULLISH_EXHAUSTION"
                
        # Bearish Fractal Confluence:
        # Macro Equilibrium (1H/30M 50%) aligns with 15M or 5M 4X Upward Exhaustion
        elif (abs(level_1h_50 - exhaust_15m_4x_up) <= 3.5 or abs(level_1h_50 - exhaust_5m_4x_up) <= 2.5 or abs(level_30m_50 - exhaust_5m_4x_up) <= 2.0):
            if abs(current_price - level_1h_50) <= 3.0 or abs(current_price - level_30m_50) <= 2.0:
                confluence_found = True
                confluence_type = "BEARISH_EXHAUSTION"
                
    except Exception as e:
        print(f"MTF Fractal calculation note: {e}")
        
    return confluence_found, confluence_type, levels

# ----------------- 4. TECHNICAL & LIQUIDITY LEVELS (1H EMA & PDH/PDL) -----------------
def get_gold_technicals():
    gold_1h = yf.download("GC=F", period="5d", interval="1h", progress=False)
    close_1h = gold_1h['Close']
    if isinstance(close_1h, pd.DataFrame):
        close_1h = close_1h.iloc[:, 0]
    
    current_price = float(close_1h.iloc[-1])
    ema_50 = float(close_1h.ewm(span=50, adjust=False).mean().iloc[-1])
    
    gold_daily = yf.download("GC=F", period="5d", interval="1d", progress=False)
    high_daily = gold_daily['High']
    low_daily = gold_daily['Low']
    if isinstance(high_daily, pd.DataFrame):
        high_daily = high_daily.iloc[:, 0]
        low_daily = low_daily.iloc[:, 0]
        
    pdh = float(high_daily.iloc[-2])
    pdl = float(low_daily.iloc[-2])
    
    return current_price, ema_50, pdh, pdl

# ----------------- 5. TELEGRAM DISPATCH -----------------
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
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Failed to dispatch Telegram alert: {e}")

# ----------------- 6. MAIN EXECUTION ENGINE -----------------
def main():
    macro_score, macro_details = get_macro_score()
    dom_score, dom_ratio = get_order_book_score()
    
    total_score = macro_score + dom_score
    current_price, ema_50, pdh, pdl = get_gold_technicals()
    confluence_found, conf_type, levels = get_mtf_fractal_confluence(current_price)
    
    signal = "NEUTRAL"
    tp = None
    sl = None
    conviction = "STANDARD"
    
    spread_buffer = 2.50
    base_sl_dist = 10.0
    
    # BUY SETUP
    if total_score >= 4 and current_price > ema_50:
        if abs(current_price - pdh) > 2.0 or current_price > pdh:
            signal = "BUY"
            if confluence_found and conf_type == "BULLISH_EXHAUSTION" and dom_ratio >= 1.20:
                conviction = "INSTITUTIONAL GRADE (MTF FRACTAL + DOM)"
                sl = round(current_price - (base_sl_dist * 0.70 + spread_buffer), 2)  # Tight Stop on Confluence
            else:
                sl = round(current_price - (base_sl_dist + spread_buffer), 2)
            tp = round(max(pdh, current_price + (base_sl_dist * 2) + spread_buffer), 2)
            
    # SELL SETUP
    elif total_score <= -4 and current_price < ema_50:
        if abs(current_price - pdl) > 2.0 or current_price < pdl:
            signal = "SELL"
            if confluence_found and conf_type == "BEARISH_EXHAUSTION" and dom_ratio <= 0.80:
                conviction = "INSTITUTIONAL GRADE (MTF FRACTAL + DOM)"
                sl = round(current_price + (base_sl_dist * 0.70 + spread_buffer), 2)  # Tight Stop on Confluence
            else:
                sl = round(current_price + (base_sl_dist + spread_buffer), 2)
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
        "Conviction": conviction,
        "SL": sl,
        "TP": tp
    }])
    
    if os.path.exists(TRADE_LOG_FILE):
        new_record.to_csv(TRADE_LOG_FILE, mode='a', header=False, index=False)
    else:
        new_record.to_csv(TRADE_LOG_FILE, index=False)
        
    # Dispatch Telegram Alert
    if signal in ["BUY", "SELL"]:
        icon = "🟢" if signal == "BUY" else "🔴"
        
        fractal_block = ""
        if confluence_found:
            fractal_block = f"""
🏛 *MTF Fractal Confluence (Exhaustion Met):*
• *1H 50% Equilibrium:* `${levels.get('1H_50', 0.0):.2f}`
• *30M 50% Zone:* `${levels.get('30M_50', 0.0):.2f}`
• *5M 4X Target:* `${levels.get('5M_4X_Down' if signal == 'BUY' else '5M_4X_Up', 0.0):.2f}`
• *DOM Absorption Ratio:* `{dom_ratio:.2f}`
"""
        msg = f"""
{icon} *INSTITUTIONAL XAU/USD ALERT: {signal}*
⚡ *Conviction Level:* `{conviction}`

📊 *Macro Score:* `{total_score}/9` | *DOM Ratio:* `{dom_ratio:.2f}`
📈 *Spot Price:* `${current_price:.2f}` | *1H 50 EMA:* `${ema_50:.2f}`
🎯 *PDH:* `${pdh:.2f}` | *PDL:* `${pdl:.2f}`
{fractal_block}
💼 *Execution Parameters:*
• *Entry:* `${current_price:.2f}`
• *Stop Loss:* `${sl:.2f}` (Spread Buffered)
• *Take Profit:* `${tp:.2f}` (Structure Target)

_Engine: Macro + MTF Fractal Matrix + Order Flow_
"""
        send_telegram_alert(msg)
        print(f"Triggered {signal} ({conviction}) alert at {current_price}")
    else:
        print(f"Scan complete. Neutral conditions (Score: {total_score}, Spot: {current_price})")

if __name__ == "__main__":
    main()
    
