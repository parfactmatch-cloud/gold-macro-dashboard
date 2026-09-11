import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

SCALP_LOG_FILE = "scalp_log.csv"

def get_structural_liquidity_levels(df_5m, lookback=20):
    """
    Extracts structural liquidity pools (swing highs and lows) from historical bars.
    """
    recent_bars = df_5m.iloc[-lookback:-1]
    liquidity_ceiling = float(recent_bars["high"].max())  # Buy-side Liquidity (Resistance)
    liquidity_floor = float(recent_bars["low"].min())     # Sell-side Liquidity (Support)
    return liquidity_ceiling, liquidity_floor

def evaluate_dynamic_scalp(df_5m, spot_price, us10y_vector):
    """
    100% Dynamic Quant Engine:
    - Adapts dynamically to ATR volatility without hardcoded prices.
    - Resolves targets via Structural Liquidity Zones instead of arbitrary fixed multiples.
    - Yield-driven directional gating and structural R:R enforcement (minimum 1:1.3).
    """
    if len(df_5m) < 30:
        return

    # 1. Bar synchronization
    df = df_5m.copy()
    df.loc[df.index[-1], "close"] = spot_price

    # 2. Dynamic indicator matrix
    ema_fast = df["close"].ewm(span=7, adjust=False).mean()
    ema_slow = df["close"].ewm(span=21, adjust=False).mean()
    
    curr_ema_fast = float(ema_fast.iloc[-1])
    curr_ema_slow = float(ema_slow.iloc[-1])
    velocity = float(ema_fast.diff().iloc[-1])

    # 3. Dynamic volatility (14-period ATR)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    atr = atr if not np.isnan(atr) and atr > 0 else 2.50

    # 4. Relative bar geometry
    curr_o = float(df["open"].iloc[-1])
    curr_h = float(df["high"].iloc[-1])
    curr_l = float(df["low"].iloc[-1])
    
    body = abs(spot_price - curr_o)
    upper_wick = curr_h - max(spot_price, curr_o)
    lower_wick = min(spot_price, curr_o) - curr_l

    # Normalized deviation from 21-EMA equilibrium
    deviation = (spot_price - curr_ema_slow) / atr

    # Structural liquidity boundary resolution
    liq_ceiling, liq_floor = get_structural_liquidity_levels(df, lookback=15)
    spread_buffer = atr * 0.15

    # =========================================================================
    # SETUP A: VOLATILITY EXHAUSTION SPIKE FADE (SHORT)
    # =========================================================================
    if deviation > 1.50 and upper_wick > (body * 1.2) and us10y_vector == "BEARISH_PRESSURE":
        sl = round(curr_h + spread_buffer, 2)
        tp = round(curr_ema_slow, 2)  # Reversion to equilibrium
        risk = sl - spot_price
        reward = spot_price - tp
        rr = reward / risk if risk > 0 else 0
        
        if rr >= 1.30:
            dispatch_execution("SHORT", spot_price, sl, tp, "VOLATILITY_EXHAUSTION_FADE")
            return
        else:
            print(f"[REJECTED] SHORT skipped: R:R {rr:.2f} below 1.3 threshold.")

    # =========================================================================
    # SETUP B: INSTITUTIONAL LIQUIDITY SWEEP (BUY)
    # =========================================================================
    rolling_low = float(df["low"].iloc[-11:-1].min())
    liquidity_trapped = (curr_l < rolling_low) and (spot_price > rolling_low) and (lower_wick > body * 1.2)
    
    if liquidity_trapped and us10y_vector != "BEARISH_PRESSURE":
        sl = round(curr_l - spread_buffer, 2)
        tp = round(liq_ceiling - spread_buffer, 2)  # Targets nearest buy-side liquidity ceiling
        risk = spot_price - sl
        reward = tp - spot_price
        rr = reward / risk if risk > 0 else 0

        if rr >= 1.30:
            dispatch_execution("BUY", spot_price, sl, tp, "LIQUIDITY_ABSORPTION_SWEEP")
            return
        else:
            print(f"[REJECTED] SWEEP BUY skipped: Ceiling at ${tp} limits R:R to {rr:.2f}.")

    # =========================================================================
    # SETUP C: EQUILIBRIUM PULLBACK RE-TEST (BUY)
    # =========================================================================
    if abs(deviation) < 0.80 and velocity > 0.04:
        if spot_price > curr_ema_slow and curr_l <= curr_ema_fast and spot_price > curr_o and us10y_vector != "BEARISH_PRESSURE":
            sl = round(curr_ema_slow - (atr * 0.40), 2)
            tp = round(liq_ceiling - spread_buffer, 2)  # Dynamic target at structural resistance
            risk = spot_price - sl
            reward = tp - spot_price
            rr = reward / risk if risk > 0 else 0

            if rr >= 1.30:
                dispatch_execution("BUY", spot_price, sl, tp, "TREND_EQUILIBRIUM_PULLBACK")
            else:
                print(f"[REJECTED] PULLBACK BUY skipped: Ceiling at ${tp} chokes R:R ({rr:.2f} < 1.30).")


def dispatch_execution(side, spot, sl, tp, regime_tag):
    if os.path.exists(SCALP_LOG_FILE):
        try:
            df_log = pd.read_csv(SCALP_LOG_FILE)
            if not df_log.empty and (df_log["status"] == "OPEN").any():
                return
        except Exception:
            pass

    risk = abs(spot - sl)
    reward = abs(tp - spot)
    rr = round(reward / risk, 2) if risk > 0 else 0
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    icon = "⚡🟢 *DYNAMIC LONG*" if side == "BUY" else "⚡🔴 *DYNAMIC SHORT*"
    card = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 *Structure Model*: `{regime_tag}`\n"
        f"💵 *Dynamic Spot*: `${spot:.2f}`\n"
        f"📐 *Risk Profile*: `SL Risk: ${risk:.2f} | Target Reward: ${reward:.2f} (1:{rr})`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 *Structural SL*: `${sl:.2f}`\n"
        f"🎯 *Liquidity Target*: `${tp:.2f}`\n"
        f"⏰ *Epoch*: `{now_utc}`"
    )
    send_telegram_msg(card)
    print(f"[{regime_tag}] {side} @ {spot} | SL: {sl} | TP: {tp} | R:R: 1:{rr}")

    trade_entry = pd.DataFrame([{
        "timestamp": now_utc, "action": side, "price": spot,
        "sl": sl, "tp": tp, "status": "OPEN", "tag": regime_tag
    }])
    trade_entry.to_csv(SCALP_LOG_FILE, mode="a", header=not os.path.exists(SCALP_LOG_FILE), index=False)

def send_telegram_msg(message: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=5
            )
        except Exception as e:
            print(f"[TG ERROR] {e}")
    
