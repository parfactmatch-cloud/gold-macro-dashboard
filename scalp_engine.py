import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, time

# ================= 1. CONFIGURATION & SECRETS =================
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCALP_LOG_FILE = "scalp_log.csv"
TRADE_LOG_FILE = "trade_log.csv"

# ================= 2. LIVE 5-MINUTE DATA FEED =================
def fetch_5m_candles(outputsize=45):
    if not TWELVE_DATA_API_KEY:
        print("[ERROR] TWELVE_DATA_API_KEY is missing.")
        return None

    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=5min&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"
    try:
        res = requests.get(url, timeout=15).json()
        if "values" not in res or not res["values"]:
            print(f"[API ERROR] Twelve Data Response: {res.get('message', 'No candle values')}")
            return None

        df = pd.DataFrame(res["values"])
        df = df.rename(columns={"datetime": "time"})
        
        numeric_cols = ["open", "high", "low", "close"]
        df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, errors="coerce")
        
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
        else:
            df["volume"] = 0

        # Sort chronologically (Oldest -> Newest)
        df = df.sort_values("time").reset_index(drop=True)
        return df

    except Exception as e:
        print(f"[FETCH ERROR] Network error fetching 5M candles: {e}")
        return None

# ================= 3. DYNAMIC ATR & ACTIVE SESSION CHECK =================
def calculate_atr(df, period=14):
    h = df['high']
    l = df['low']
    c = df['close']
    tr1 = h - l
    tr2 = (h - c.shift(1)).abs()
    tr3 = (l - c.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) and atr > 0 else 1.80

def is_active_session():
    now_utc = datetime.now(timezone.utc).time()
    return time(7, 0) <= now_utc <= time(21, 0)

# ================= 4. MACRO BIAS ALIGNMENT =================
def get_macro_bias():
    """
    Reads trade_log.csv to get the latest macro engine score.
    Prevents fighting higher-timeframe institutional liquidity.
    """
    if not os.path.exists(TRADE_LOG_FILE):
        return 0
    try:
        df = pd.read_csv(TRADE_LOG_FILE, on_bad_lines="skip")
        if "Macro_Score" in df.columns and not df.empty:
            return float(df["Macro_Score"].iloc[-1])
    except Exception:
        pass
    return 0

# ================= 5. 7-EMA RETEST + DT PRICE RANGE ENGINE =================
def detect_scalp_setup(df):
    if df is None or len(df) < 25:
        return {"trigger": False, "action": "NONE"}

    df["ema_7"] = df["close"].ewm(span=7, adjust=False).mean()
    
    lookback = 20
    swing_high = df["high"].iloc[-lookback:].max()
    swing_low = df["low"].iloc[-lookback:].min()
    base_range = max(swing_high - swing_low, 1.5)
    
    dt_upper_4x = swing_high + (base_range * 3)
    dt_lower_4x = swing_low - (base_range * 3)

    in_buy_zone = (df["low"].iloc[-1] <= (swing_low + (base_range * 0.3))) or (df["low"].iloc[-1] <= dt_lower_4x)
    in_sell_zone = (df["high"].iloc[-1] >= (swing_high - (base_range * 0.3))) or (df["high"].iloc[-1] >= dt_upper_4x)

    curr_c = df["close"].iloc[-1]
    curr_o = df["open"].iloc[-1]
    curr_h = df["high"].iloc[-1]
    curr_l = df["low"].iloc[-1]
    curr_ema = df["ema_7"].iloc[-1]

    bullish_trigger = in_buy_zone and (curr_l <= curr_ema) and (curr_c > curr_ema) and (curr_c > curr_o)
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

# ================= 6. ACTIVE POSITION TRACKER (TP/SL/BE) =================
def monitor_active_trades(current_high, current_low, current_price):
    """
    Checks if previously alerted trades hit TP, SL, or Breakeven.
    """
    if not os.path.exists(SCALP_LOG_FILE):
        return
    try:
        df = pd.read_csv(SCALP_LOG_FILE, on_bad_lines="skip")
        if df.empty or "status" not in df.columns:
            return

        open_trades = df[df["status"] == "OPEN"]
        if open_trades.empty:
            return

        for idx, trade in open_trades.iterrows():
            action = trade["action"]
            entry = float(trade["price"])
            sl = float(trade["sl"])
            tp = float(trade["tp"])
            be_sent = bool(trade.get("be_alerted", False))

            if action == "SCALP_BUY":
                if current_high >= tp:
                    send_exit_telegram("🎯 TAKE PROFIT HIT", action, entry, tp, f"+${round(tp - entry, 2)}")
                    df.at[idx, "status"] = "CLOSED_TP"
                elif current_low <= sl:
                    send_exit_telegram("🛑 STOP LOSS HIT", action, entry, sl, f"-${round(entry - sl, 2)}")
                    df.at[idx, "status"] = "CLOSED_SL"
                elif not be_sent and current_price >= (entry + (abs(entry - sl) * 1.5)):
                    send_be_telegram(action, entry)
                    df.at[idx, "be_alerted"] = True

            elif action == "SCALP_SELL":
                if current_low <= tp:
                    send_exit_telegram("🎯 TAKE PROFIT HIT", action, entry, tp, f"+${round(entry - tp, 2)}")
                    df.at[idx, "status"] = "CLOSED_TP"
                elif current_high >= sl:
                    send_exit_telegram("🛑 STOP LOSS HIT", action, entry, sl, f"-${round(sl - entry, 2)}")
                    df.at[idx, "status"] = "CLOSED_SL"
                elif not be_sent and current_price <= (entry - (abs(entry - sl) * 1.5)):
                    send_be_telegram(action, entry)
                    df.at[idx, "be_alerted"] = True

        df.to_csv(SCALP_LOG_FILE, index=False)
    except Exception as e:
        print(f"[MONITOR ERROR] {e}")

# ================= 7. LOGGING & TELEGRAM DISPATCH =================
def is_duplicate_alert(action, price):
    if not os.path.exists(SCALP_LOG_FILE):
        return False
    try:
        df = pd.read_csv(SCALP_LOG_FILE, on_bad_lines="skip")
        signals = df[df["action"].isin(["SCALP_BUY", "SCALP_SELL"])]
        if signals.empty:
            return False
        last_sig = signals.iloc[-1]
        if last_sig["action"] == action and abs(float(last_sig["price"]) - price) <= 1.5:
            return True
    except Exception:
        pass
    return False

def record_scalp_log(action, price, ema_7, sl=0.0, tp=0.0, atr=0.0, status="RECORD"):
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    cols = ["timestamp", "action", "price", "ema_7", "sl", "tp", "atr", "status", "be_alerted"]
    
    new_data = pd.DataFrame([{
        "timestamp": now_utc,
        "action": action,
        "price": round(price, 2),
        "ema_7": round(ema_7, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "atr": round(atr, 2),
        "status": status,
        "be_alerted": False
    }])

    if not os.path.exists(SCALP_LOG_FILE):
        new_data.to_csv(SCALP_LOG_FILE, index=False)
    else:
        new_data.to_csv(SCALP_LOG_FILE, mode="a", header=False, index=False)
    print(f"[LOGGED] {action} at ${price:.2f} (SL: ${sl:.2f}, TP: ${tp:.2f})")

def send_scalp_telegram(action, price, ema_7, sl, tp, atr, zone, macro_score):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    icon = "⚡🟢 *5M SCALP BUY*" if action == "SCALP_BUY" else "⚡🔴 *5M SCALP SELL*"
    msg = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Strategy*: `7-EMA Retest + Dynamic ATR`\n"
        f"📍 *Asset*: `XAU/USD (5M Spot)`\n"
        f"💵 *Entry Price*: `${price:.2f}`\n"
        f"📈 *7 EMA Anchor*: `${ema_7:.2f}`\n"
        f"📊 *Volatility (5M ATR)*: `${atr:.2f}`\n"
        f"🏛 *Macro Alignment*: `Score {macro_score:.1f}`\n"
        f"🧱 *Context*: `{zone}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 *Stop Loss*: `${sl:.2f}` (Dynamic 1.5x ATR)\n"
        f"🎯 *Take Profit*: `${tp:.2f}` (Strict 1:2 R:R)\n"
        f"⏰ *Time (UTC)*: `{datetime.now(timezone.utc).strftime('%H:%M:%S')}`\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ _Rule: System will trigger Breakeven alert at +1.5R._"
    )
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10
    )

def send_exit_telegram(title, action, entry, exit_price, pnl):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    msg = (
        f"{title}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 *Asset*: `XAU/USD 5M`\n"
        f"🔄 *Position*: `{action}`\n"
        f"💵 *Entry*: `${entry:.2f}` ➔ *Exit*: `${exit_price:.2f}`\n"
        f"💰 *Net Move*: `{pnl}`\n"
        f"⏰ *Time (UTC)*: `{datetime.now(timezone.utc).strftime('%H:%M:%S')}`"
    )
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10
    )

def send_be_telegram(action, entry):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    msg = (
        f"🛡️ *BREAKEVEN ALERT (+1.5R REACHED)* 🛡️\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Position: `{action}`\n"
        f"Move Stop Loss to: `${entry:.2f}`\n"
        f"Status: *Trade is now completely Risk-Free!*"
    )
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
        timeout=10
    )

# ================= 8. MAIN EXECUTION LOOP =================
def run():
    print(f"[SCALPER RUN] UTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    
    if not is_active_session():
        print("[SCALPER HOLD] Asian off-hours session. Gate locked.")
        record_scalp_log("SESSION_LOCKED", 0.0, 0.0)
        return

    df_5m = fetch_5m_candles(outputsize=45)
    if df_5m is None or len(df_5m) < 20:
        print("[SCALPER HOLD] Unable to process 5M candles.")
        record_scalp_log("DATA_FETCH_FAILED", 0.0, 0.0)
        return

    current_price = df_5m["close"].iloc[-1]
    current_high = df_5m["high"].iloc[-1]
    current_low = df_5m["low"].iloc[-1]
    atr = calculate_atr(df_5m)

    # 1. First monitor any open position
    monitor_active_trades(current_high, current_low, current_price)

    # 2. Check for new setup
    setup = detect_scalp_setup(df_5m)
    macro_score = get_macro_bias()

    if setup["trigger"]:
        action = setup["action"]
        ema_val = setup["ema_7"]
        zone = setup.get("zone", "Price Exhaustion Zone")

        # Macro Alignment Gatekeeper:
        # Bullish scalp only if Macro >= 0, Bearish scalp only if Macro <= 0
        if action == "SCALP_BUY" and macro_score < 0:
            print(f"[FILTER BLOCKED] SCALP_BUY blocked due to Bearish Macro ({macro_score})")
            return
        if action == "SCALP_SELL" and macro_score > 0:
            print(f"[FILTER BLOCKED] SCALP_SELL blocked due to Bullish Macro ({macro_score})")
            return

        # Dynamic ATR-Based SL & TP (1.5x ATR Risk, 3.0x ATR Reward = 1:2 R:R)
        sl_buffer = round(atr * 1.5, 2)
        tp_buffer = round(sl_buffer * 2.0, 2)

        if action == "SCALP_BUY":
            sl = round(current_price - sl_buffer, 2)
            tp = round(current_price + tp_buffer, 2)
        else:
            sl = round(current_price + sl_buffer, 2)
            tp = round(current_price - tp_buffer, 2)

        if not is_duplicate_alert(action, current_price):
            send_scalp_telegram(action, current_price, ema_val, sl, tp, atr, zone, macro_score)
            record_scalp_log(action, current_price, ema_val, sl, tp, atr, status="OPEN")
        else:
            print(f"[SCALPER DEDUP] Repeated alert skipped for price ${current_price:.2f}")
    else:
        print(f"[NO SETUP] Price: ${current_price:.2f} | 7-EMA: ${setup.get('ema_7', 0)} | ATR: ${atr:.2f}")
        record_scalp_log("NO_SETUP", current_price, setup.get("ema_7", 0), atr=atr)

if __name__ == "__main__":
    run()
    
