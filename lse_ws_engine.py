"""
===============================================================================
PROJECT: LSE REAL-TIME WEBSOCKET SCALPING ENGINE (XAU/USD)
STREAM: wss://data-ws.londonstrategicedge.com
LATENCY: SUB-SECOND REAL-TIME TICK EXECUTION
===============================================================================
"""

import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from lse import LSE

# ================= CONFIGURATION =================
LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCALP_LOG_FILE = "scalp_log.csv"
EMA_FAST = 7
EMA_SLOW = 21

# In-memory candle aggregator
current_candle = None
candle_history = []
CANDLE_INTERVAL_SEC = 300  # 5 Minutes

def dispatch_telegram(text: str):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                timeout=5
            )
        except Exception as e:
            print(f"[TG ERROR] {e}")

def evaluate_instant_scalp(df_5m, spot_price):
    if len(df_5m) < 25:
        return

    close_s = df_5m["close"].copy()
    close_s.iloc[-1] = spot_price  # Inject current live tick into current bar close

    ema_7 = close_s.ewm(span=EMA_FAST, adjust=False).mean()
    ema_21 = close_s.ewm(span=EMA_SLOW, adjust=False).mean()
    velocity = ema_7.diff().iloc[-1]

    curr_ema7 = ema_7.iloc[-1]
    curr_ema21 = ema_21.iloc[-1]
    curr_l = df_5m["low"].iloc[-1]
    curr_o = df_5m["open"].iloc[-1]

    # Calculate dynamic 14 ATR
    tr = pd.concat([
        df_5m['high'] - df_5m['low'],
        (df_5m['high'] - close_s.shift(1)).abs(),
        (df_5m['low'] - close_s.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    atr = atr if not np.isnan(atr) and atr > 0 else 2.50

    # 1. Instant Long Trigger on 7-EMA Rebound
    if velocity > 0.04 and spot_price > curr_ema21:
        if curr_l <= (curr_ema7 + 0.35) and spot_price > curr_ema7 and spot_price > curr_o:
            trigger_order("BUY", spot_price, curr_ema7, atr, velocity)

    # 2. Instant Short Trigger on 7-EMA Rejection
    elif velocity < -0.04 and spot_price < curr_ema21:
        curr_h = df_5m["high"].iloc[-1]
        if curr_h >= (curr_ema7 - 0.35) and spot_price < curr_ema7 and spot_price < curr_o:
            trigger_order("SELL", spot_price, curr_ema7, atr, velocity)

def trigger_order(side, spot, ema7, atr, vel):
    # Concurrency Lock: Check if already open
    if os.path.exists(SCALP_LOG_FILE):
        try:
            df_log = pd.read_csv(SCALP_LOG_FILE)
            if not df_log.empty and (df_log["status"] == "OPEN").any():
                return
        except Exception:
            pass

    risk = round(atr * 1.5, 2)
    sl = round(spot - risk, 2) if side == "BUY" else round(spot + risk, 2)
    tp = round(spot + (risk * 2.0), 2) if side == "BUY" else round(spot - (risk * 2.0), 2)
    utc_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    icon = "⚡🟢 *WEBSOCKET LIVE BUY*" if side == "BUY" else "⚡🔴 *WEBSOCKET LIVE SELL*"
    card = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ *Latency*: `Zero Latency (Tick Stream)`\n"
        f"💵 *Spot Trigger*: `${spot:.2f}`\n"
        f"📐 *EMA-7 Velocity*: `v={vel:+.2f}`\n"
        f"📊 *5M Volatility (ATR)*: `${atr:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 *Dynamic SL*: `${sl:.2f}`\n"
        f"🎯 *Quant Target (2R)*: `${tp:.2f}`\n"
        f"⏰ *Epoch*: `{utc_str}`"
    )
    dispatch_telegram(card)
    print(f"[TRIGGER] {side} at ${spot:.2f}")

    # Save state
    new_trade = pd.DataFrame([{
        "timestamp": utc_str, "action": f"SCALP_{side}", "price": spot,
        "ema_7": ema7, "sl": sl, "tp": tp, "atr": atr, "status": "OPEN", "be_alerted": "False"
    }])
    new_trade.to_csv(SCALP_LOG_FILE, mode="a", header=not os.path.exists(SCALP_LOG_FILE), index=False)

def start_lse_stream():
    if not LSE_API_KEY:
        print("[ERROR] LSE_API_KEY is not set.")
        return

    print(f"[SYSTEM] Initializing LSE WebSocket Stream for XAU/USD...")
    client = LSE(api_key=LSE_API_KEY)

    global current_candle, candle_history

    # Subscribe to live streaming ticks
    for tick in client.stream("XAU/USD"):  # Uses wss://data-ws.londonstrategicedge.com
        try:
            price = float(tick.price)
            now = time.time()
            candle_start = now - (now % CANDLE_INTERVAL_SEC)

            if current_candle is None or current_candle["time"] != candle_start:
                if current_candle is not None:
                    candle_history.append(current_candle)
                    if len(candle_history) > 60:
                        candle_history.pop(0)

                current_candle = {
                    "time": candle_start,
                    "open": price, "high": price, "low": price, "close": price
                }
            else:
                current_candle["high"] = max(current_candle["high"], price)
                current_candle["low"] = min(current_candle["low"], price)
                current_candle["close"] = price

            # Real-time evaluation on every tick
            if len(candle_history) >= 20:
                df = pd.DataFrame(candle_history + [current_candle])
                evaluate_instant_scalp(df, price)

        except Exception as e:
            print(f"[STREAM ERROR] {e}")

if __name__ == "__main__":
    start_lse_stream()
  
