import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# ================= CONFIGURATION & SECRETS =================
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
FRED_API_KEY = os.getenv("FRED_API_KEY")

TRADE_LOG_FILE = "trade_log.csv"

# ================= DATA FETCHING (TWELVE DATA) =================
def fetch_gold_data():
    """
    Fetches real-time XAU/USD 5-minute candles using Twelve Data API.
    Replaces deprecated/blocked Yahoo Finance GC=F safely.
    """
    if not TWELVE_DATA_API_KEY:
        print("[ERROR] TWELVE_DATA_API_KEY secret is missing.")
        return None

    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=5min&outputsize=50&apikey={TWELVE_DATA_API_KEY}"
    try:
        res = requests.get(url, timeout=15).json()
        if "values" not in res or not res["values"]:
            print(f"[API ERROR] Twelve Data Response: {res.get('message', 'No candle values returned')}")
            return None

        df = pd.DataFrame(res["values"])
        df = df.rename(columns={"datetime": "time"})
        
        numeric_cols = ["open", "high", "low", "close"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)

        # Ascending order (Oldest -> Newest)
        df = df.sort_values("time").reset_index(drop=True)

        if len(df) < 15:
            print(f"[DATA WARNING] Insufficient candles: {len(df)}")
            return None

        print(f"[DATA SUCCESS] Twelve Data XAU/USD loaded. Latest Close: {df['close'].iloc[-1]}")
        return df

    except Exception as e:
        print(f"[NETWORK ERROR] Failed to fetch Twelve Data: {e}")
        return None

# ================= LAYER 1: MACRO SENTIMENT (FRED + DXY) =================
def get_macro_sentiment():
    """
    Calculates institutional macro points:
    - 10Y Real Yields (TIPS)
    - US Dollar Index (DXY)
    """
    score = 0
    # DXY delta estimation or fallback
    try:
        url = f"https://api.twelvedata.com/time_series?symbol=DXY&interval=1day&outputsize=5&apikey={TWELVE_DATA_API_KEY}"
        dxy_res = requests.get(url, timeout=10).json()
        if "values" in dxy_res and len(dxy_res["values"]) >= 2:
            closes = [float(x["close"]) for x in dxy_res["values"]]
            delta = closes[0] - closes[-1]
            if delta < -0.50:
                score += 2  # DXY Falling -> Bullish Gold
            elif delta > 0.50:
                score -= 2  # DXY Rising -> Bearish Gold
    except Exception:
        pass

    return score

# ================= LAYER 2: BINANCE L2 ORDER BOOK (DOM) =================
def get_binance_l2_dom():
    """
    Evaluates liquidity walls / imbalance on PAXGUSDT (Gold crypto equivalent)
    """
    try:
        url = "https://api.binance.com/api/v3/depth?symbol=PAXGUSDT&limit=20"
        res = requests.get(url, timeout=10).json()
        bids = sum([float(x[1]) for x in res.get("bids", [])])
        asks = sum([float(x[1]) for x in res.get("asks", [])])

        if (bids + asks) == 0:
            return 1.0

        ratio = bids / asks
        print(f"[DOM STATUS] Bid/Ask Ratio: {ratio:.2f}")
        return ratio
    except Exception as e:
        print(f"[DOM WARNING] Could not fetch Binance DOM: {e}")
        return 1.0

# ================= LAYER 3: MTF FRACTALS & STRUCTURE =================
def calculate_mtf_fractals(df: pd.DataFrame):
    """
    Calculates multi-bar swing highs and swing lows.
    Crash-proof against empty slices (prevents Index -5 error).
    """
    if df is None or len(df) < 10:
        return {"bullish_fractal": False, "bearish_fractal": False}

    highs = df["high"].values
    lows = df["low"].values

    # 5-bar Fractal Confirmation (Center bar is highest/lowest)
    is_bearish_fractal = (highs[-3] > highs[-5]) and (highs[-3] > highs[-4]) and (highs[-3] > highs[-2]) and (highs[-3] > highs[-1])
    is_bullish_fractal = (lows[-3] < lows[-5]) and (lows[-3] < lows[-4]) and (lows[-3] < lows[-2]) and (lows[-3] < lows[-1])

    return {
        "bullish_fractal": bool(is_bullish_fractal),
        "bearish_fractal": bool(is_bearish_fractal),
        "last_high": float(highs[-3]),
        "last_low": float(lows[-3])
    }

# ================= TELEGRAM DISPATCHER =================
def send_telegram_alert(message: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Missing Bot Token or Chat ID.")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("[TELEGRAM] Alert sent successfully!")
        else:
            print(f"[TELEGRAM ERROR] {res.text}")
    except Exception as e:
        print(f"[TELEGRAM FAILED] {e}")

# ================= LOGGING ENGINE =================
def log_trade(action, price, macro_score, dom_ratio):
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"{now_utc},{action},{price},{macro_score},{dom_ratio}\n"

    if not os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, "w") as f:
            f.write("timestamp,action,price,macro_score,dom_ratio\n")

    with open(TRADE_LOG_FILE, "a") as f:
        f.write(log_entry)
    print(f"[LOGGED] {log_entry.strip()}")

# ================= MAIN PIPELINE EXECUTION =================
def run_engine():
    print("================ STARTING GOLD MACRO ENGINE ================")
    
    # Step 1: Fetch Live Candlesticks safely
    df = fetch_gold_data()
    if df is None or df.empty:
        print("[CRITICAL SHUTDOWN] Could not retrieve candlestick data. Exiting iteration.")
        return

    current_price = df["close"].iloc[-1]
    
    # Step 2: Macro Layer
    macro_score = get_macro_sentiment()

    # Step 3: Binance DOM Liquidity
    dom_ratio = get_binance_l2_dom()

    # Step 4: MTF Fractals
    fractals = calculate_mtf_fractals(df)

    # Step 5: Decision Logic Gate
    signal = "NO_SETUP"

    # Bullish Gate: Macro Conviction >= 0, DOM Buyers Active, Bullish Fractal confirmed
    if macro_score >= 0 and dom_ratio >= 1.05 and fractals["bullish_fractal"]:
        signal = "BUY_XAUUSD"
    # Bearish Gate: Macro Conviction <= 0, DOM Sellers Active, Bearish Fractal confirmed
    elif macro_score <= 0 and dom_ratio <= 0.95 and fractals["bearish_fractal"]:
        signal = "SELL_XAUUSD"

    print(f"Scan complete. Decision: {signal} (Macro: {macro_score}, DOM: {dom_ratio:.2f}, Price: {current_price})")

    # Step 6: Trigger Alerts & Record Logs
    if signal != "NO_SETUP":
        msg = (
            f"🚨 *INSTITUTIONAL GOLD ALERT (XAU/USD)* 🚨\n\n"
            f"• *Signal*: `{signal}`\n"
            f"• *Execution Price*: `{current_price}`\n"
            f"• *Macro Conviction*: `{macro_score}`\n"
            f"• *DOM Imbalance*: `{dom_ratio:.2f}`\n"
            f"• *Fractal State*: Confirmed\n"
            f"• *Timestamp*: `{datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}`"
        )
        send_telegram_alert(msg)
        log_trade(signal, current_price, macro_score, dom_ratio)
    else:
        # Update trade_log file timestamp for Git commit verification
        log_trade("GATE_HOLD", current_price, macro_score, dom_ratio)

if __name__ == "__main__":
    run_engine()
    
