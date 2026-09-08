"""
===============================================================================
PROJECT: QUANTITATIVE HIGH-FREQUENCY SCALPING ENGINE (XAU/USD)
ARCHITECTURE: INSTITUTIONAL MONEY-FLOW ORIGIN + 7-EMA EULER DYNAMICS
VERSION: 3.6 (BUG FIX: SAFE NON-SERIES VOLUME EXTRACTION + EVT TAIL-RISK)
===============================================================================
"""

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

# Hyperparameters (Quant Calibration)
EMA_FAST = 7
EMA_SLOW = 21
LOOKBACK_SWING = 20
VOLATILITY_LOOKBACK = 14
MIN_SLOPE_THRESHOLD = 0.04  # Minimum first derivative magnitude (USD/candle)

# ================= 2. DATA ACQUISITION (FIXED) =================
def fetch_time_series(interval="5min", n_bars=50):
    if not TWELVE_DATA_API_KEY:
        print("[ERROR] TWELVE_DATA_API_KEY missing.")
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": "XAU/USD",
        "interval": interval,
        "outputsize": n_bars,
        "apikey": TWELVE_DATA_API_KEY
    }
    try:
        res = requests.get(url, params=params, timeout=12).json()
        if "values" not in res or not res["values"]:
            print(f"[API ERROR] {interval}: {res.get('message', 'No values returned')}")
            return None
        
        df = pd.DataFrame(res["values"]).rename(columns={"datetime": "timestamp"})
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Bug Fix: Safe extraction without calling Series methods on missing keys
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
        else:
            df["volume"] = 0.0

        return df.sort_values("timestamp").reset_index(drop=True)
    except Exception as e:
        print(f"[FETCH ERROR] {interval}: {e}")
        return None

# ================= 3. 30M REGIME & INSTITUTIONAL MONEY FLOW =================
def compute_30m_regime_and_origin(df_30m):
    """
    Evaluates:
    1. Higher-Timeframe (30M) Structure (HH/HL vs LH/LL)
    2. Institutional Money Flow Origin (Base/VWAP Cost Anchor)
    """
    if df_30m is None or len(df_30m) < 20:
        return "REGIME_NEUTRAL", 0.0, 0.0

    close = df_30m["close"].values
    high = df_30m["high"].values
    low = df_30m["low"].values

    h_win = high[-8:]
    l_win = low[-8:]

    is_hh = (h_win[-1] > h_win[-3]) and (h_win[-3] > h_win[-5])
    is_hl = (l_win[-1] > l_win[-3]) and (l_win[-3] > l_win[-5])
    is_lh = (h_win[-1] < h_win[-3]) and (h_win[-3] < h_win[-5])
    is_ll = (l_win[-1] < l_win[-3]) and (l_win[-3] < l_win[-5])

    ema_20_30m = pd.Series(close).ewm(span=20, adjust=False).mean().iloc[-1]
    curr_c = close[-1]

    bullish_origin_base = low[-10:].min()   # Base liquidity where buyers entered
    bearish_origin_base = high[-10:].max()  # Supply cap where profit was booked

    if (is_hh or is_hl) and curr_c > ema_20_30m:
        regime = "BULLISH_DRIFT"
    elif (is_lh or is_ll) and curr_c < ema_20_30m:
        regime = "BEARISH_DRIFT"
    else:
        regime = "MEAN_REVERTING_RANGE"

    return regime, bullish_origin_base, bearish_origin_base

# ================= 4. MATHEMATICAL KERNELS & DERIVATIVES =================
def calculate_derivatives(series, span=7):
    """
    First Derivative (Velocity): v(t) = EMA(t) - EMA(t-1)
    Second Derivative (Acceleration): a(t) = v(t) - v(t-1)
    """
    ema = series.ewm(span=span, adjust=False).mean()
    velocity = ema.diff()
    acceleration = velocity.diff()
    return ema, velocity, acceleration

def calculate_parkinson_atr(df, period=VOLATILITY_LOOKBACK):
    h, l, c = df['high'], df['low'], df['close']
    tr1 = h - l
    tr2 = (h - c.shift(1)).abs()
    tr3 = (l - c.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) and atr > 0 else 1.80

def compute_dt_exhaustion(df_5m, lookback=LOOKBACK_SWING):
    sub_df = df_5m.iloc[-lookback:]
    h_max = sub_df["high"].max()
    l_min = sub_df["low"].min()
    delta_r = max(h_max - l_min, 1.50)

    upper_exhaustion = h_max + (delta_r * 2.0)
    lower_exhaustion = l_min - (delta_r * 2.0)

    return h_max, l_min, delta_r, upper_exhaustion, lower_exhaustion

def is_active_liquidity_window():
    """Core London/NY overlap session only (07:00 to 18:30 UTC)."""
    now_t = datetime.now(timezone.utc).time()
    return time(7, 0) <= now_t <= time(18, 30)

# ================= 5. QUANT SCALP & TAIL-RISK DISCRIMINATION =================
def evaluate_quant_scalp(df_5m, regime_30m, bull_origin, bear_origin):
    if df_5m is None or len(df_5m) < 30:
        return {"signal": False, "action": "NONE"}

    close_s = df_5m["close"]
    ema_7, vel_7, acc_7 = calculate_derivatives(close_s, span=EMA_FAST)
    ema_21 = close_s.ewm(span=EMA_SLOW, adjust=False).mean()

    curr_c = close_s.iloc[-1]
    curr_o = df_5m["open"].iloc[-1]
    curr_h = df_5m["high"].iloc[-1]
    curr_l = df_5m["low"].iloc[-1]

    current_ema7 = ema_7.iloc[-1]
    current_ema21 = ema_21.iloc[-1]
    v_t = vel_7.iloc[-1]
    a_t = acc_7.iloc[-1]

    _, _, _, dt_upper, dt_lower = compute_dt_exhaustion(df_5m)

    # 1. LONG CRITERIA (Respects Institutional Support Origin + 7-EMA Rebound)
    long_regime_valid = regime_30m in ["BULLISH_DRIFT", "MEAN_REVERTING_RANGE"]
    long_ema_gradient = (v_t > MIN_SLOPE_THRESHOLD) and (a_t >= -0.02) and (curr_c > current_ema21)
    long_microstructure = (curr_l <= (current_ema7 + 0.35)) and (curr_c > current_ema7) and (curr_c > curr_o)
    long_tail_risk_safe = curr_c < (dt_upper - 1.0)
    long_origin_aligned = curr_c >= bull_origin

    if long_regime_valid and long_ema_gradient and long_microstructure and long_tail_risk_safe and long_origin_aligned:
        return {
            "signal": True,
            "action": "SCALP_BUY",
            "ema_7": current_ema7,
            "velocity": v_t,
            "acceleration": a_t,
            "regime": regime_30m
        }

    # 2. SHORT CRITERIA (Respects Institutional Supply Origin + 7-EMA Rejection)
    short_regime_valid = regime_30m in ["BEARISH_DRIFT", "MEAN_REVERTING_RANGE"]
    short_ema_gradient = (v_t < -MIN_SLOPE_THRESHOLD) and (a_t <= 0.02) and (curr_c < current_ema21)
    short_microstructure = (curr_h >= (current_ema7 - 0.35)) and (curr_c < current_ema7) and (curr_c < curr_o)
    short_tail_risk_safe = curr_c > (dt_lower + 1.0)
    short_origin_aligned = curr_c <= bear_origin

    if short_regime_valid and short_ema_gradient and short_microstructure and short_tail_risk_safe and short_origin_aligned:
        return {
            "signal": True,
            "action": "SCALP_SELL",
            "ema_7": current_ema7,
            "velocity": v_t,
            "acceleration": a_t,
            "regime": regime_30m
        }

    return {
        "signal": False,
        "action": "NONE",
        "ema_7": current_ema7,
        "velocity": v_t,
        "acceleration": a_t,
        "regime": regime_30m
    }

# ================= 6. AUTOMATED TRADE MONITOR & AUDIT =================
def audit_open_positions(high_t, low_t, close_t):
    if not os.path.exists(SCALP_LOG_FILE):
        return
    try:
        df = pd.read_csv(SCALP_LOG_FILE, on_bad_lines="skip")
        if df.empty or "status" not in df.columns:
            return

        open_idx = df[df["status"] == "OPEN"].index
        for i in open_idx:
            row = df.loc[i]
            entry = float(row["price"])
            sl = float(row["sl"])
            tp = float(row["tp"])
            action = row["action"]
            be_active = bool(row.get("be_alerted", False))

            if action == "SCALP_BUY":
                if high_t >= tp:
                    dispatch_telegram(f"🎯 *QUANT TAKE PROFIT EXECUTED (+2.0R)*\nSide: `BUY` | Entry: `${entry:.2f}` ➔ Exit: `${tp:.2f}`\nAlpha: `+${round(tp - entry, 2)}`")
                    df.at[i, "status"] = "CLOSED_TP"
                elif low_t <= sl:
                    dispatch_telegram(f"🛑 *QUANT STOP LOSS HIT (-1.0R)*\nSide: `BUY` | Entry: `${entry:.2f}` ➔ Exit: `${sl:.2f}`\nLoss: `-${round(entry - sl, 2)}`")
                    df.at[i, "status"] = "CLOSED_SL"
                elif not be_active and close_t >= (entry + (abs(entry - sl) * 1.5)):
                    dispatch_telegram(f"🛡️ *BREAKEVEN REACHED (+1.5R)*\nSide: `BUY` | Move SL to `${entry:.2f}` (Risk-Free State)")
                    df.at[i, "be_alerted"] = True

            elif action == "SCALP_SELL":
                if low_t <= tp:
                    dispatch_telegram(f"🎯 *QUANT TAKE PROFIT EXECUTED (+2.0R)*\nSide: `SELL` | Entry: `${entry:.2f}` ➔ Exit: `${tp:.2f}`\nAlpha: `+${round(entry - tp, 2)}`")
                    df.at[i, "status"] = "CLOSED_TP"
                elif high_t >= sl:
                    dispatch_telegram(f"🛑 *QUANT STOP LOSS HIT (-1.0R)*\nSide: `SELL` | Entry: `${entry:.2f}` ➔ Exit: `${sl:.2f}`\nLoss: `-${round(sl - entry, 2)}`")
                    df.at[i, "status"] = "CLOSED_SL"
                elif not be_active and close_t <= (entry - (abs(entry - sl) * 1.5)):
                    dispatch_telegram(f"🛡️ *BREAKEVEN REACHED (+1.5R)*\nSide: `SELL` | Move SL to `${entry:.2f}` (Risk-Free State)")
                    df.at[i, "be_alerted"] = True

        df.to_csv(SCALP_LOG_FILE, index=False)
    except Exception as e:
        print(f"[AUDIT_ERROR] {e}")

# ================= 7. LOGGING & TELEGRAM DISPATCH =================
def dispatch_telegram(text):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )

def log_state(action, price, ema_7, sl=0.0, tp=0.0, atr=0.0, status="RECORD"):
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    payload = pd.DataFrame([{
        "timestamp": now_utc, "action": action, "price": round(price, 2),
        "ema_7": round(ema_7, 2), "sl": round(sl, 2), "tp": round(tp, 2),
        "atr": round(atr, 2), "status": status, "be_alerted": False
    }])
    if not os.path.exists(SCALP_LOG_FILE):
        payload.to_csv(SCALP_LOG_FILE, index=False)
    else:
        payload.to_csv(SCALP_LOG_FILE, mode="a", header=False, index=False)

# ================= 8. MAIN EXECUTION PIPELINE =================
def run():
    utc_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[QUANT_SYS_CLOCK] Tick UTC: {utc_str}")

    if not is_active_liquidity_window():
        print("[RISK_GATE] Suppressed: Non-liquid session window (Outside 07:00-18:30 UTC).")
        log_state("SESSION_LOCKED", 0.0, 0.0)
        return

    df_5m = fetch_time_series("5min", 50)
    df_30m = fetch_time_series("30min", 35)

    if df_5m is None or len(df_5m) < 30:
        print("[DATA_FAULT] Incomplete candle series.")
        log_state("DATA_FAULT", 0.0, 0.0)
        return

    c_price = df_5m["close"].iloc[-1]
    h_price = df_5m["high"].iloc[-1]
    l_price = df_5m["low"].iloc[-1]
    atr_val = calculate_parkinson_atr(df_5m)

    # 1. Audit inventory state
    audit_open_positions(h_price, l_price, c_price)

    # 2. HTF Regime & Cost Origin Check
    regime_30m, bull_origin, bear_origin = compute_30m_regime_and_origin(df_30m)

    # 3. Microstructure Vector Evaluation
    decision = evaluate_quant_scalp(df_5m, regime_30m, bull_origin, bear_origin)

    if decision["signal"]:
        side = decision["action"]
        ema_val = decision["ema_7"]
        v = decision["velocity"]
        a = decision["acceleration"]

        risk_unit = round(atr_val * 1.5, 2)
        reward_unit = round(risk_unit * 2.0, 2)

        sl = round(c_price - risk_unit, 2) if side == "SCALP_BUY" else round(c_price + risk_unit, 2)
        tp = round(c_price + reward_unit, 2) if side == "SCALP_BUY" else round(c_price - reward_unit, 2)

        icon = "⚡🟢 *QUANT LONG SCALP*" if side == "SCALP_BUY" else "⚡🔴 *QUANT SHORT SCALP*"
        card = (
            f"{icon}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 *30M Regime Vector*: `{regime_30m}`\n"
            f"📐 *EMA-7 Dynamics*: `v={v:+.2f} | a={a:+.2f}`\n"
            f"🏛 *Money Flow Anchor*: `${bull_origin:.2f if side=='SCALP_BUY' else bear_origin:.2f}`\n"
            f"💵 *Execution Spot*: `${c_price:.2f}`\n"
            f"📊 *Volatility (5M ATR)*: `${atr_val:.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🛑 *Dynamic Risk (1.0R)*: `${sl:.2f}`\n"
            f"🎯 *Quant Target (2.0R)*: `${tp:.2f}`\n"
            f"🛡️ *Breakeven Threshold*: `+1.5R Vector Move`\n"
            f"⏰ *Epoch*: `{utc_str} UTC`"
        )
        dispatch_telegram(card)
        log_state(side, c_price, ema_val, sl, tp, atr_val, status="OPEN")
        print(f"[ORDER_PLACED] {side} at ${c_price:.2f}")
    else:
        print(f"[EQUILIBRIUM] Regime: {regime_30m} | Price: ${c_price:.2f} | EMA7: ${decision['ema_7']:.2f} | v={decision['velocity']:+.2f}")
        log_state("NO_TRIGGER", c_price, decision["ema_7"], atr=atr_val)

if __name__ == "__main__":
    run()
    
