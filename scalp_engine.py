import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, time

# ================= 1. CONFIGURATION & SECRETS =================
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SCALP_LOG_FILE = "scalp_log.csv"

# ================= 2. LIVE 5-MINUTE DATA FEED =================
def fetch_5m_candles(outputsize=40):
    if not TWELVE_DATA_API_KEY:
        print("[ERROR] TWELVE_DATA_API_KEY is not set.")
        return None

    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=5min&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
    try:
        res = requests.get(url, timeout=12).json()
        if "values" not in res or not res["values"]:
            print(f"[API ERROR] Twelve Data Response: {res.get('message', 'No candle values')}")
            return None

        df = pd.DataFrame(res["values"])
        df = df.rename(columns={"datetime": "time"})
        
        numeric_cols = ["open", "high", "low", "close"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)

        # Sort chronologically (Oldest -> Newest)
        df = df.sort_values("time").reset_index(drop=True)
        return df
    except Exception as e:
        print(f"[FETCH ERROR] Network error fetching 5M candles: {e}")
        return None

# ================= 3. ACTIVE SESSION CHECK =================
def is_active_session():
    """
    Filters high-volume hours:
    London Open + NY Session + MCX Prime (07:00 UTC to 21:00 UTC)
    """
    now_utc = datetime.now(timezone.utc).time()
    return time(7, 0) <= now_utc <= time(21, 0)

# ================= 4. 7-EMA RETEST + DT PRICE RANGE ENGINE =================
def detect_scalp_setup(df):
    if df is None or len(df) < 25:
        return {"trigger": False, "action": "NONE"}

    # 1. 7 EMA Calculation
    df["ema_7"] = df["close"].ewm(span=7, adjust=False).mean()
    
    # 2. DT Price Range (Last 20 candles base swing)
    lookback = 20
    swing_high = df["high"].iloc[-lookback:].max()
    swing_low = df["low"].iloc[-lookback:].min()
    base_range = max(swing_high - swing_low, 1.5)
    
    dt_upper_4x = swing_high + (base_range * 3)
    dt_lower_4x = swing_low - (base_range * 3)

    # Zone Buffers (Base Range ka 30%)
    in_buy_zone = (df["low"].iloc[-1] <= (swing_low + (base_range * 0.3))) or (df["low"].iloc[-1] <= dt_lower_4x)
    in_sell_zone = (df["high"].iloc[-1] >= (swing_high - (base_range * 0.3))) or (df["high"].iloc[-1] >= dt_upper_4x)

    # 3. 7-EMA Retest & Candlestick Confirmation
    curr_c = df["close"].iloc[-1]
    curr_o = df["open"].iloc[-1]
    curr_h = df["high"].iloc[-1]
    curr_l = df["low"].iloc[-1]
    curr_ema = df["ema_7"].iloc[-1]

    # Bullish Bounce: 7 EMA ko touch/pierce kiya, par close EMA ke upar aur green candle bani
    bullish_trigger = in_buy_zone and (curr_l <= curr_ema) and (curr_c > curr_ema) and (curr_c > curr_o)

    # Bearish Rejection: 7 EMA ko touch/pierce kiya, par close EMA ke niche aur red candle bani
    bearish_trigger = in_sell_zone and (curr_h >= curr_ema) and (curr_c < curr_ema) and (curr_c < curr_o)

    if bullish_trigger:
        return {
            "trigger": True,
            "action": "SCALP_BUY",
            "ema_7": round(curr_ema, 2),
            "zone": "DT Support / Reversal Zone",
            "base_range": round(base_range, 2)
        }
    elif bearish_trigger:
        return {
            "trigger": True,
            "action": "SCALP_SELL",
            "ema_7": round(curr_ema, 2),
            "zone": "DT Resistance / Exhaustion Zone",
            "base_range": round(base_range, 2)
        }

    return {"trigger": False, "action": "NONE", "ema_7": round(curr_ema, 2)}

# ================= 5. DEDUPLICATION & LOGGING =================
def is_duplicate_alert(action, price):
    if not os.path.exists(SCALP_LOG_FILE):
        return False
    try:
        df = pd.read_csv(SCALP_LOG_FILE, on_bad_lines="skip")
        signals = df[df["action"].isin(["SCALP_BUY", "SCALP_SELL"])]
        if signals.empty:
            return False
        last_sig = signals.iloc[-1]
        # Agar picche 15 minute ke andar same action aur similar price (<= $1.5) par alert gaya ho
        if last_sig["action"] == action and abs(float(last_sig["price"]) - price) <= 1.5:
            return True
    except Exception:
        pass
    return False

def record_scalp_log(action, price, ema_7):
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    header = "timestamp,action,price,ema_7\n"
    entry = f"{now_utc},{action},{price:.2f},{ema_7:.2f}\n"

    if not os.path.exists(SCALP_LOG_FILE):
        with open(SCALP_LOG_FILE, "w") as f:
            f.write(header)

    with open(SCALP_LOG_FILE, "a") as f:
        f.write(entry)

# ================= 6. TELEGRAM NOTIFIER =================
def send_scalp_telegram(action, price, ema_7, sl, tp, zone):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] Missing Bot Token or Chat ID.")
        return

    icon = "⚡🟢 *5M SCALP BUY*" if action == "SCALP_BUY" else "⚡🔴 *5M SCALP SELL*"
    msg = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Strategy*: `7-EMA Retest Bounce`\n"
        f"📍 *Asset*: `XAU/USD (5M Spot)`\n"
        f"💵 *Entry Price*: `${price:.2f}`\n"
        f"📈 *7 EMA Anchor*: `${ema_7:.2f}`\n"
        f"🧱 *Context*: `{zone}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 *Stop Loss*: `${sl:.2f}` (Tight Structural)\n"
        f"🎯 *Take Profit*: `${tp:.2f}` (1:2 R:R Ratio)\n"
        f"⏰ *Time (UTC)*: `{datetime.now(timezone.utc).strftime('%H:%M:%S')}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ _Rule: Trail stop to breakeven once price moves +1.5R._"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            print(f"[TELEGRAM SUCCESS] Dispatched {action} alert!")
    except Exception as e:
        print(f"[TELEGRAM FAIL] {e}")

# ================= 7. MAIN EXECUTION LOOP =================
def run():
    print(f"[SCALPER RUN] UTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    
    # Step 1: Session Gate
    if not is_active_session():
        print("[SCALPER HOLD] Asian off-hours session. Gate locked to prevent noise.")
        return

    # Step 2: Fetch 5M Candles
    df_5m = fetch_5m_candles(outputsize=40)
    if df_5m is None or len(df_5m) < 20:
        print("[SCALPER HOLD] Unable to fetch 5M candles.")
        return

    current_price = df_5m["close"].iloc[-1]
    setup = detect_scalp_setup(df_5m)

    # Step 3: Trigger Evaluation
    if setup["trigger"]:
        action = setup["action"]
        ema_val = setup["ema_7"]
        zone = setup.get("zone", "Price Exhaustion Zone")

        # 1:2 Dynamic Scalp Parameters ($2.5 SL, $5.0 TP)
        if action == "SCALP_BUY":
            sl = round(current_price - 2.50, 2)
            tp = round(current_price + 5.00, 2)
        else:
            sl = round(current_price + 2.50, 2)
            tp = round(current_price - 5.00, 2)

        if not is_duplicate_alert(action, current_price):
            send_scalp_telegram(action, current_price, ema_val, sl, tp, zone)
            record_scalp_log(action, current_price, ema_val)
        else:
            print(f"[SCALPER DEDUP] Repeated alert skipped for price {current_price}")
    else:
        print(f"[NO SETUP] Latest: ${current_price:.2f} | 7-EMA: ${setup.get('ema_7', 0)}")
        record_scalp_log("NO_SETUP", current_price, setup.get("ema_7", 0))

if __name__ == "__main__":
    run()
      
