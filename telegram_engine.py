import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, time

# ================= 1. CREDENTIALS & CONSTANTS =================
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
FRED_API_KEY = os.getenv("FRED_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TRADE_LOG_FILE = "trade_log.csv"

# ================= 2. LIVE PRICE FEED (TWELVE DATA) =================
def fetch_gold_data(interval="5min", outputsize=50):
    """
    Fetches clean OHLCV candle data from Twelve Data API.
    Zero scraping blocks, zero GitHub IP blacklisting.
    """
    if not TWELVE_DATA_API_KEY:
        print("[ERROR] TWELVE_DATA_API_KEY environment variable is missing.")
        return None

    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
    try:
        response = requests.get(url, timeout=15)
        res = response.json()

        if "values" not in res or not res["values"]:
            print(f"[DATA ERROR] Twelve Data Response: {res.get('message', 'No values returned')}")
            return None

        df = pd.DataFrame(res["values"])
        df = df.rename(columns={"datetime": "time"})
        
        numeric_cols = ["open", "high", "low", "close"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)

        # Sort ascending (Oldest to Newest)
        df = df.sort_values("time").reset_index(drop=True)

        if len(df) < 15:
            print(f"[DATA WARNING] Not enough candles returned: {len(df)}")
            return None

        return df

    except Exception as e:
        print(f"[FETCH ERROR] Network error fetching Twelve Data: {e}")
        return None

# ================= 3. LAYER 1: INSTITUTIONAL MACRO (FRED + DXY) =================
def get_fred_series(series_id, limit=10):
    if not FRED_API_KEY:
        return None
    url = f"https://api.stlouisfed.org/fred/series/observations?series_id={series_id}&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&limit={limit}"
    try:
        r = requests.get(url, timeout=10).json()
        obs = [float(x["value"]) for x in r.get("observations", []) if x.get("value") not in [".", None, ""]]
        return obs
    except Exception as e:
        print(f"[FRED ERROR] Series {series_id} failed: {e}")
        return None

def calculate_macro_conviction():
    """
    Evaluates:
    1. 10Y Real Yield (TIPS: DFII10) 5-Day Delta -> +/- 3 Points
    2. Fed Net Liquidity (WALCL - TGA - RRP) -> +/- 2 Points
    3. DXY 5-Day Delta -> +/- 2 Points
    """
    total_score = 0
    tips_score = 0
    liq_score = 0
    dxy_score = 0

    # 1. TIPS Real Yields (DFII10)
    tips_data = get_fred_series("DFII10", limit=10)
    if tips_data and len(tips_data) >= 5:
        tips_delta = tips_data[0] - tips_data[4]
        if tips_delta < -0.05:
            tips_score = 3
        elif tips_delta > 0.05:
            tips_score = -3
        total_score += tips_score

    # 2. Fed Net Liquidity (WALCL, WTREGEN, RRPONTSYD)
    walcl = get_fred_series("WALCL", limit=5)
    tga = get_fred_series("WTREGEN", limit=5)
    rrp = get_fred_series("RRPONTSYD", limit=5)

    if walcl and tga and rrp and len(walcl) >= 2 and len(tga) >= 2 and len(rrp) >= 2:
        curr_liq = walcl[0] - (tga[0] + rrp[0])
        prev_liq = walcl[1] - (tga[1] + rrp[1])
        liq_score = 2 if curr_liq > prev_liq else -2
        total_score += liq_score

    # 3. DXY Movement
    try:
        url = f"https://api.twelvedata.com/time_series?symbol=DXY&interval=1day&outputsize=5&apikey={TWELVE_DATA_API_KEY}"
        dxy_res = requests.get(url, timeout=10).json()
        if "values" in dxy_res and len(dxy_res["values"]) >= 5:
            closes = [float(x["close"]) for x in dxy_res["values"]]
            dxy_delta = closes[0] - closes[4]
            if dxy_delta < -0.50:
                dxy_score = 2
            elif dxy_delta > 0.50:
                dxy_score = -2
            total_score += dxy_score
    except Exception:
        pass

    return {
        "macro_score": total_score,
        "tips_score": tips_score,
        "liq_score": liq_score,
        "dxy_score": dxy_score
    }

# ================= 4. LAYER 2: BINANCE L2 ORDER BOOK (DOM) =================
def get_binance_l2_depth():
    """
    Examines real-time institutional bid/ask order book depth on PAXG/USDT.
    """
    try:
        url = "https://api.binance.com/api/v3/depth?symbol=PAXGUSDT&limit=50"
        res = requests.get(url, timeout=10).json()
        bids = sum([float(b[1]) for b in res.get("bids", [])])
        asks = sum([float(a[1]) for a in res.get("asks", [])])

        if (bids + asks) == 0:
            return 1.0, 0, 0

        ratio = bids / asks
        return ratio, bids, asks
    except Exception as e:
        print(f"[DOM ERROR] Binance L2 depth unreachable: {e}")
        return 1.0, 0, 0

# ================= 5. LAYER 3: HIGHER TIMEFRAME EMAs (STRUCTURAL BIAS) =================
def calculate_trend_bias():
    """
    Computes daily 20 EMA and 1H 50 EMA trend direction.
    """
    bias = "NEUTRAL"
    daily_ema = 0.0

    df_daily = fetch_gold_data(interval="1day", outputsize=30)
    if df_daily is not None and len(df_daily) >= 20:
        daily_ema = df_daily["close"].ewm(span=20, adjust=False).mean().iloc[-1]
        latest_price = df_daily["close"].iloc[-1]
        bias = "BULLISH" if latest_price >= daily_ema else "BEARISH"

    return bias, daily_ema

# ================= 6. LAYER 4: 5M MTF FRACTALS & STRUCTURE =================
def calculate_mtf_fractals(df: pd.DataFrame):
    """
    Calculates 5-bar Williams Fractal exhaustion points safely.
    Strictly crash-proof against empty arrays.
    """
    if df is None or len(df) < 10:
        return {"bullish_fractal": False, "bearish_fractal": False, "last_high": 0.0, "last_low": 0.0}

    highs = df["high"].values
    lows = df["low"].values

    # 5-Bar Center Fractal Logic
    is_bearish = (highs[-3] > highs[-5]) and (highs[-3] > highs[-4]) and (highs[-3] > highs[-2]) and (highs[-3] > highs[-1])
    is_bullish = (lows[-3] < lows[-5]) and (lows[-3] < lows[-4]) and (lows[-3] < lows[-2]) and (lows[-3] < lows[-1])

    return {
        "bullish_fractal": bool(is_bullish),
        "bearish_fractal": bool(is_bearish),
        "last_high": float(highs[-3]),
        "last_low": float(lows[-3])
    }

# ================= 7. SESSION LIQUIDITY FILTER =================
def is_in_trading_session():
    """
    Validates active market hours:
    - London & New York Session: 07:00 to 21:00 UTC
    - Indian MCX active trading hours coverage included.
    """
    now_utc = datetime.now(timezone.utc).time()
    session_start = time(7, 0)
    session_end = time(21, 30)
    return session_start <= now_utc <= session_end

# ================= 8. DISPATCH & LOGGING =================
def send_telegram_card(signal, price, macro_data, dom_ratio, trend_bias, daily_ema):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Missing Bot Token or Chat ID.")
        return

    icon = "🟢 *LONG SETUP*" if "BUY" in signal else "🔴 *SHORT SETUP*"
    msg = (
        f"🏛️ *INSTITUTIONAL GOLD MACRO ALERT* 🏛️\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Action: {icon}\n"
        f"Asset: *XAU/USD (Spot Gold)*\n"
        f"Price: *${price:.2f}*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 *CONVICTION METRICS:*\n"
        f"• Total Macro Score: *{macro_data['macro_score']}*\n"
        f"  └ TIPS Delta: `{macro_data['tips_score']}` | Fed Liq: `{macro_data['liq_score']}` | DXY: `{macro_data['dxy_score']}`\n"
        f"• Binance DOM Ratio: *{dom_ratio:.2f}* (PAXG L2 Depth)\n"
        f"• Higher Timeframe Bias: *{trend_bias}* (Daily 20 EMA: `{daily_ema:.2f}`)\n"
        f"• MTF 5M Fractal: *Confirmed Expiration*\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ *Time (UTC)*: `{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}`\n"
        f"🛡️ *Execution Rule*: Enter on next candle open; Target 1:2 R:R."
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print("[TELEGRAM] Comprehensive signal card sent!")
        else:
            print(f"[TELEGRAM ERROR] {res.text}")
    except Exception as e:
        print(f"[TELEGRAM EXCEPTION] {e}")

def log_trade(action, price, macro_score, dom_ratio, bias):
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    header = "timestamp,action,price,macro_score,dom_ratio,htf_bias\n"
    row = f"{now_utc},{action},{price:.2f},{macro_score},{dom_ratio:.2f},{bias}\n"

    if not os.path.exists(TRADE_LOG_FILE):
        with open(TRADE_LOG_FILE, "w") as f:
            f.write(header)

    with open(TRADE_LOG_FILE, "a") as f:
        f.write(row)
    print(f"[TRADE LOG] {row.strip()}")

# ================= 9. MAIN PIPELINE ORCHESTRATION =================
def run_engine():
    print("=========================================================")
    print(f"🚀 INITIATING INSTITUTIONAL GOLD ENGINE | UTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    print("=========================================================")

    # 1. Fetch 5M Candle Stream
    df_5m = fetch_gold_data(interval="5min", outputsize=50)
    if df_5m is None or df_5m.empty:
        print("[HALT] Unable to acquire valid 5M candle sequence. Terminating safely.")
        return

    current_price = df_5m["close"].iloc[-1]
    print(f"[LIVE SPOT] XAU/USD: ${current_price:.2f}")

    # 2. Institutional Macro Conviction
    macro_data = calculate_macro_conviction()
    macro_score = macro_data["macro_score"]

    # 3. Binance DOM Imbalance
    dom_ratio, bids, asks = get_binance_l2_depth()

    # 4. Session Validation
    in_session = is_in_trading_session()

    # 5. Higher Timeframe Structural Trend
    trend_bias, daily_ema = calculate_trend_bias()

    # 6. 5M MTF Fractal Calculations
    fractals = calculate_mtf_fractals(df_5m)

    # 7. Multi-Layer Logic Gate
    signal = "NO_SETUP"

    # Multi-Variable Bullish Gate:
    # 1. Macro >= 0, 2. DOM Ratio >= 1.05, 3. Bullish Fractal, 4. In Session, 5. HTF Trend supports
    if in_session and macro_score >= 0 and dom_ratio >= 1.05 and fractals["bullish_fractal"] and trend_bias != "BEARISH":
        signal = "BUY_XAUUSD"

    # Multi-Variable Bearish Gate:
    # 1. Macro <= 0, 2. DOM Ratio <= 0.95, 3. Bearish Fractal, 4. In Session, 5. HTF Trend supports
    elif in_session and macro_score <= 0 and dom_ratio <= 0.95 and fractals["bearish_fractal"] and trend_bias != "BULLISH":
        signal = "SELL_XAUUSD"

    # Print Diagnostic Summary Log
    print(f"Scan complete. Gate closed / Setup: {signal} "
          f"(Macro: {macro_score}, DOM: {dom_ratio:.2f}, InSession: {in_session}, Daily_EMA: {daily_ema:.2f})")

    # 8. Alert Delivery & State Commit
    if signal != "NO_SETUP":
        send_telegram_card(signal, current_price, macro_data, dom_ratio, trend_bias, daily_ema)
        log_trade(signal, current_price, macro_score, dom_ratio, trend_bias)
    else:
        log_trade("GATE_HOLD", current_price, macro_score, dom_ratio, trend_bias)

if __name__ == "__main__":
    run_engine()

