"""
dynamic_engine.py
Institutional Dynamic Execution & GEX Gatekeeper Engine.
Incorporates Dynamic Basis Normalization (Futures/GLD -> CFD Spot),
Dealer Net Delta (DEX) Alignment, Structural Liquidity Gating, and Cooldown Guards.
"""

import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from free_gex_engine import GoldGEXEngine

SCALP_LOG_FILE = "scalp_log.csv"

def get_structural_liquidity_levels(df_5m, lookback=20):
    """
    Extracts structural liquidity pools (swing highs and lows) from historical bars.
    """
    recent_bars = df_5m.iloc[-lookback:-1]
    liquidity_ceiling = float(recent_bars["high"].max())  # Buy-side Liquidity (Resistance)
    liquidity_floor = float(recent_bars["low"].min())     # Sell-side Liquidity (Support)
    return liquidity_ceiling, liquidity_floor

def get_normalized_gex_levels(current_spot: float) -> dict:
    """
    Normalizes GEX walls to CFD spot domain if there is an active Futures/Spot basis gap.
    """
    gex_data = GoldGEXEngine.read_cached_levels()
    if gex_data.get("status") in ["FAILED", "NO_CACHE"]:
        return gex_data

    call_wall = float(gex_data.get("call_wall_xau", float("inf")))
    put_wall = float(gex_data.get("put_wall_xau", 0.0))
    cached_spot = float(gex_data.get("spot_xau", current_spot))

    # Calculate dynamic basis if cached GEX spot is mapped from higher Futures domain
    basis = cached_spot - current_spot if abs(cached_spot - current_spot) > 10.0 else 0.0

    gex_data["norm_call_wall"] = round(call_wall - basis, 2)
    gex_data["norm_put_wall"] = round(put_wall - basis, 2)
    gex_data["basis_spread"] = round(basis, 2)
    return gex_data

def check_gex_gatekeeper(direction: str, current_price: float, atr_14: float) -> tuple[bool, str, float]:
    """
    Advanced Institutional Gatekeeper:
    1. Blocks trades firing directly into dealer Gamma Walls (with Dynamic Basis alignment).
    2. DEX Flow Gating: Blocks shorts when Dealer Net Delta is positive (+DEX).
    """
    gex_data = get_normalized_gex_levels(current_price)
    if gex_data.get("status") in ["FAILED", "NO_CACHE"]:
        return True, "GEX_BYPASS_NO_CACHE", 0.0

    call_wall = gex_data.get("norm_call_wall", float("inf"))
    put_wall = gex_data.get("norm_put_wall", 0.0)
    net_dex_m = float(gex_data.get("net_dex_m", 0.0))
    buffer_zone = max(0.5 * atr_14, 1.50)

    # 1. DEX Directional Flow Check
    if direction == "SHORT" and net_dex_m > 25.0:
        return False, f"BLOCKED_BY_POSITIVE_DEX (Dealer Net Long: +{net_dex_m}M absorbing sell-off)", put_wall

    if direction == "BUY" and net_dex_m < -500.0 and not gex_data.get("gamma_blast_active", False):
        return False, f"BLOCKED_BY_EXTREME_NEGATIVE_DEX (Dealer Net Short: {net_dex_m}M)", call_wall

    # 2. Wall Proximity Barriers
    if direction == "BUY":
        dist_to_call = call_wall - current_price
        if 0 <= dist_to_call <= buffer_zone:
            return False, f"BLOCKED_BY_CALL_WALL (Wall: {call_wall:.2f}, Cushion: {buffer_zone:.2f})", call_wall

    elif direction == "SHORT":
        dist_to_put = current_price - put_wall
        if 0 <= dist_to_put <= buffer_zone:
            return False, f"BLOCKED_BY_PUT_WALL (Wall: {put_wall:.2f}, Cushion: {buffer_zone:.2f})", put_wall

    return True, "GEX_PERMITTED", 0.0

def evaluate_dynamic_scalp(df_5m, spot_price, us10y_vector):
    """
    100% Dynamic Quant Engine:
    - Yield-driven directional gating & Structural R:R enforcement.
    - Normalized GEX Wall and DEX Alignment Gatekeepers.
    - Late-Chop & Exhaustion Trapping Guards.
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
    liq_ceiling, liq_floor = get_structural_liquidity_levels(df, lookback=20)
    spread_buffer = atr * 0.15

    # =========================================================================
    # SETUP A: VOLATILITY EXHAUSTION SPIKE FADE (SHORT)
    # =========================================================================
    if deviation > 1.50 and upper_wick > (body * 1.2) and us10y_vector == "BEARISH_PRESSURE":
        # Guard: Stop late-shorting if spot is already pinned directly on liquidity floor
        if abs(spot_price - liq_floor) <= (atr * 0.75):
            print(f"[REJECTED] SHORT skipped: Extended move resting directly on liquidity floor ${liq_floor:.2f}")
            return

        sl = round(curr_h + spread_buffer, 2)
        tp = round(curr_ema_slow, 2)
        risk = sl - spot_price
        reward = spot_price - tp
        rr = reward / risk if risk > 0 else 0
        
        if rr >= 1.30:
            gex_ok, gex_reason, _ = check_gex_gatekeeper("SHORT", spot_price, atr)
            if not gex_ok:
                print(f"[REJECTED] SHORT skipped: {gex_reason}")
                return

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
        tp = round(liq_ceiling - spread_buffer, 2)
        risk = spot_price - sl
        reward = tp - spot_price
        rr = reward / risk if risk > 0 else 0

        if rr >= 1.30:
            gex_ok, gex_reason, _ = check_gex_gatekeeper("BUY", spot_price, atr)
            if not gex_ok:
                print(f"[REJECTED] SWEEP BUY skipped: {gex_reason}")
                return

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
            tp = round(liq_ceiling - spread_buffer, 2)
            risk = spot_price - sl
            reward = tp - spot_price
            rr = reward / risk if risk > 0 else 0

            if rr >= 1.30:
                gex_ok, gex_reason, _ = check_gex_gatekeeper("BUY", spot_price, atr)
                if not gex_ok:
                    print(f"[REJECTED] PULLBACK BUY skipped: {gex_reason}")
                    return

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

    # Read normalized GEX metadata for the alert card
    gex_data = get_normalized_gex_levels(spot)
    call_wall = gex_data.get("norm_call_wall", gex_data.get("call_wall_xau", "N/A"))
    put_wall = gex_data.get("norm_put_wall", gex_data.get("put_wall_xau", "N/A"))
    basis = gex_data.get("basis_spread", 0.0)

    basis_str = f" (Basis Adj: -${basis:.2f})" if basis > 0 else ""

    icon = "⚡🟢 *DYNAMIC LONG*" if side == "BUY" else "⚡🔴 *DYNAMIC SHORT*"
    card = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 *Structure Model*: `{regime_tag}`\n"
        f"💵 *Broker Spot (CFD)*: `${spot:.2f}`\n"
        f"📐 *Risk Profile*: `Risk: ${risk:.2f} | Target: ${reward:.2f} (1:{rr})`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 *CFD Stop-Loss*: `${sl:.2f}`\n"
        f"🎯 *Liquidity Target*: `${tp:.2f}`\n"
        f"🛡️ *GEX Call Wall*: `${call_wall}`{basis_str}\n"
        f"🛡️ *GEX Put Wall*: `${put_wall}`{basis_str}\n"
        f"⚖️ *DEX Regime*: `{gex_data.get('net_dex_m', 0.0)}M`\n"
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
    
