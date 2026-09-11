"""
===============================================================================
PROJECT: LSE ULTRA-LOW LATENCY REAL-TIME SCALPING ENGINE (XAU/USD)
ARCHITECTURE: Zero Disk-I/O Latency | Non-Blocking Async Alerts | 100% Dynamic Quant
===============================================================================
"""

import os
import time
import threading
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
CANDLE_INTERVAL_SEC = 300  # 5 Minutes

# Global State
candle_history = []
current_candle = None
has_open_trade = False  # In-memory Concurrency Lock

# ================= NON-BLOCKING TELEGRAM =================
def async_telegram(text: str):
    """Dispatches Telegram notifications in a daemon thread to prevent WS loop stutter."""
    def _worker():
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            try:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
                    timeout=5
                )
            except Exception as e:
                print(f"[TG ERROR] {e}")
    threading.Thread(target=_worker, daemon=True).start()

# ================= COLD-START HISTORY SEEDER =================
def bootstrap_history(client):
    """Pre-populates 5M history via REST so the engine evaluates immediately on first tick."""
    global candle_history
    print("[SYSTEM] Fetching 35 historical 5M bars for instant warm start...")
    try:
        bars = client.get_bars("XAU/USD", timeframe="5m", limit=35)
        for b in bars:
            candle_history.append({
                "time": int(b.timestamp),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close)
            })
        print(f"[SYSTEM] Warm start complete: {len(candle_history)} bars buffered.")
    except Exception as e:
        print(f"[WARN] Failed to preload history ({e}). Will buffer live ticks.")

# ================= DYNAMIC QUANT KERNEL =================
def evaluate_dynamic_scalp(df_5m: pd.DataFrame, spot_price: float, macro_vector: str = "BEARISH_PRESSURE"):
    global has_open_trade
    if has_open_trade or len(df_5m) < 25:
        return

    # 1. Real-time Series Injection
    close_series = df_5m["close"].copy()
    close_series.iloc[-1] = spot_price

    # 2. Dynamic Moving Averages
    ema_fast = close_series.ewm(span=7, adjust=False).mean()
    ema_slow = close_series.ewm(span=21, adjust=False).mean()
    curr_ema_fast = float(ema_fast.iloc[-1])
    curr_ema_slow = float(ema_slow.iloc[-1])
    velocity = float(ema_fast.diff().iloc[-1])

    # 3. Dynamic Volatility (14 ATR)
    tr = pd.concat([
        df_5m['high'] - df_5m['low'],
        (df_5m['high'] - close_series.shift(1)).abs(),
        (df_5m['low'] - close_series.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    atr = atr if not np.isnan(atr) and atr > 0 else 2.50

    # 4. Bar Geometry & Relative Deviation
    curr_o = float(df_5m["open"].iloc[-1])
    curr_h = float(df_5m["high"].iloc[-1])
    curr_l = float(df_5m["low"].iloc[-1])

    body = abs(spot_price - curr_o)
    upper_wick = curr_h - max(spot_price, curr_o)
    lower_wick = min(spot_price, curr_o) - curr_l

    # Deviation from Equilibrium (Normalized by ATR)
    deviation = (spot_price - curr_ema_slow) / atr

    # Structural Range (Past 10 Closed Bars)
    rolling_low = float(df_5m["low"].iloc[-11:-1].min())
    rolling_high = float(df_5m["high"].iloc[-11:-1].max())

    # ------------------ STRATEGY 1: EXHAUSTION SPIKE FADE (SHORT) ------------------
    # Trigger: Price overextended (>1.5x ATR) + Rejection Upper Wick + Bearish Macro Pressure
    if deviation > 1.50 and upper_wick > (body * 1.1) and macro_vector == "BEARISH_PRESSURE":
        sl = round(curr_h + (atr * 0.20), 2)
        tp = round(curr_ema_slow, 2)  # Return to Mean Target
        execute_trade("SHORT", spot_price, sl, tp, "VOLATILITY_EXHAUSTION_FADE", atr)
        return

    # ------------------ STRATEGY 2: LIQUIDITY SWEEP SNIPER (BUY) ------------------
    # Trigger: False breakdown of rolling base + Absorbed wick + Macro is not hostile
    is_sweep_absorbed = (curr_l < rolling_low) and (spot_price > rolling_low) and (lower_wick > body * 1.2)
    if is_sweep_absorbed and macro_vector != "BEARISH_PRESSURE":
        sl = round(curr_l - (atr * 0.20), 2)
        risk = spot_price - sl
        tp = round(spot_price + (risk * 2.0), 2)
        execute_trade("BUY", spot_price, sl, tp, "LIQUIDITY_ABSORPTION_SWEEP", atr)
        return

    # ------------------ STRATEGY 3: STRUCTURAL PULLBACK BUY ------------------
    # Anti-FOMO Guard: Must be close to equilibrium (|deviation| <= 0.8)
    if abs(deviation) <= 0.80 and velocity > 0.04 and spot_price > curr_ema_slow:
        if curr_l <= (curr_ema_fast + 0.35) and spot_price > curr_ema_fast and spot_price >= curr_o:
            if macro_vector != "BEARISH_PRESSURE":
                sl = round(curr_ema_slow - (atr * 0.40), 2)
                risk = spot_price - sl
                tp = round(spot_price + (risk * 2.0), 2)
                execute_trade("BUY", spot_price, sl, tp, "EQUILIBRIUM_PULLBACK", atr)

# ================= EXECUTION & PERSISTENCE =================
def execute_trade(side: str, spot: float, sl: float, tp: float, setup_type: str, atr: float):
    global has_open_trade
    has_open_trade = True  # Instant memory lock

    risk = abs(spot - sl)
    reward = abs(tp - spot)
    rr = round(reward / risk, 2) if risk > 0 else 0
    utc_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    icon = "⚡🟢 *DYNAMIC BUY TRIGGER*" if side == "BUY" else "⚡🔴 *DYNAMIC FADE SHORT*"
    card = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 *Engine Setup*: `{setup_type}`\n"
        f"💵 *Execution Spot*: `${spot:.2f}`\n"
        f"📐 *5M Volatility (ATR)*: `${atr:.2f}`\n"
        f"⚖️ *Risk Envelope*: `Risk: ${risk:.2f} | R:R: 1:{rr}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 *Dynamic SL*: `${sl:.2f}`\n"
        f"🎯 *Quant Target*: `${tp:.2f}`\n"
        f"⏰ *Epoch*: `{utc_str}`"
    )
    async_telegram(card)
    print(f"[{setup_type}] {side} @ ${spot:.2f} | SL: ${sl:.2f} | TP: ${tp:.2f}")

    # Async disk append
    def _disk_writer():
        trade_entry = pd.DataFrame([{
            "timestamp": utc_str, "action": f"SCALP_{side}", "price": spot,
            "sl": sl, "tp": tp, "atr": atr, "status": "OPEN", "setup": setup_type
        }])
        trade_entry.to_csv(SCALP_LOG_FILE, mode="a", header=not os.path.exists(SCALP_LOG_FILE), index=False)
    threading.Thread(target=_disk_writer, daemon=True).start()

# ================= REAL-TIME STREAMING WORKER =================
def start_lse_stream():
    if not LSE_API_KEY:
        print("[CRITICAL] LSE_API_KEY environment variable is not defined.")
        return

    client = LSE(api_key=LSE_API_KEY)
    bootstrap_history(client)

    global current_candle, candle_history

    print(f"[SYSTEM] Connecting to LSE WebSocket Feed (wss://data-ws.londonstrategicedge.com)...")
    
    # Tick-Level Streaming Loop
    for tick in client.stream("XAU/USD"):
        try:
            price = float(tick.price)
            now = time.time()
            candle_start = int(now - (now % CANDLE_INTERVAL_SEC))

            # New 5M Bar Shift
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
                # Intra-bar Dynamic Updates
                if price > current_candle["high"]:
                    current_candle["high"] = price
                elif price < current_candle["low"]:
                    current_candle["low"] = price
                current_candle["close"] = price

            # Real-time Evaluation on every streaming tick
            if len(candle_history) >= 25:
                df = pd.DataFrame(candle_history + [current_candle])
                evaluate_dynamic_scalp(df, price, macro_vector="BEARISH_PRESSURE")

        except Exception as e:
            print(f"[TICK ERROR] {e}")

if __name__ == "__main__":
    start_lse_stream()
            
