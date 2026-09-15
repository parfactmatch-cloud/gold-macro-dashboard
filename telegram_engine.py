"""
===============================================================================
SYSTEM: INSTITUTIONAL SYSTEMATIC MACRO & QUANT CONFLUENCE ENGINE (XAU/USD)
METHODOLOGY: LSE YIELD VECTORS, DEALER GEX/DEX & GAMMA BLAST EXEMPTION GATE
ROLE: PRODUCTION TELEMETRY & QUANT EXECUTION ENGINE (PATCHED & ALIGNED)
===============================================================================
"""

import os
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from fredapi import Fred
from typing import Dict, Tuple, Optional, Any

# GEX Integration Layer
from free_gex_engine import GoldGEXEngine

# ================= 1. SYSTEM PARAMETERS & CONFIGURATION =================
FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN", "") 
    or os.getenv("BOT_TOKEN", "")
).strip()
TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID", "") 
    or os.getenv("CHAT_ID", "")
).strip()
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()

TRADE_LOG_FILE = "trade_log.csv"
SCALP_LOG_FILE = "scalp_log.csv"
LSE_MACRO_FILE = "lse_macro.csv"

# Systematic Decision Hyperparameters
ALPHA_THRESHOLD_LONG = 3.0
ALPHA_THRESHOLD_SHORT = -3.0
DOM_ASYMMETRY_BID_MIN = 1.20
DOM_ASYMMETRY_ASK_MAX = 0.85
VOLATILITY_LOOKBACK = 14
DYNAMIC_ATR_MULTIPLIER = 1.25
CONFLUENCE_EQUILIBRIUM_TOLERANCE = 3.0
MIN_RR_THRESHOLD = 1.30

# ================= 2. DATA INGESTION MATRIX =================
def fetch_twelve_data(interval: str = "1h", outputsize: int = 30) -> Optional[pd.DataFrame]:
    if not TWELVE_DATA_API_KEY:
        return None

    td_interval_map = {
        "1h": "1h",
        "30m": "30min",
        "15m": "15min",
        "5m": "5min",
        "1d": "1day"
    }
    api_interval = td_interval_map.get(interval, "1h")
    url = f"https://api.twelvedata.com/time_series?symbol=XAU/USD&interval={api_interval}&outputsize={outputsize}&apikey={TWELVE_DATA_API_KEY}"

    try:
        res = requests.get(url, timeout=12).json()
        if "values" not in res or not res["values"]:
            return None

        df = pd.DataFrame(res["values"]).rename(columns={"datetime": "time"})
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
            
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
        else:
            df["volume"] = 0.0
            
        return df.sort_values("time").reset_index(drop=True)
    except Exception:
        return None

# ================= 3. REAL-TIME SPOT PRICE & ORDER BOOK =================
def get_live_gold_spot() -> Optional[float]:
    """
    Fetches spot gold reference. Prioritizes TwelveData XAU/USD if available,
    with fallback to Paxos Gold on Binance.
    """
    if TWELVE_DATA_API_KEY:
        try:
            url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_API_KEY}"
            res = requests.get(url, timeout=4).json()
            if "price" in res:
                return float(res["price"])
        except Exception:
            pass

    endpoints = [
        "https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT",
        "https://data-api.binance.vision/api/v3/ticker/price?symbol=PAXGUSDT"
    ]
    for url in endpoints:
        try:
            res = requests.get(url, timeout=4).json()
            if isinstance(res, dict) and 'price' in res:
                return float(res['price'])
        except Exception:
            continue
    return None

def compute_order_book_skew() -> Tuple[float, float]:
    endpoints = [
        "https://api.binance.com/api/v3/depth?symbol=PAXGUSDT&limit=20",
        "https://data-api.binance.vision/api/v3/depth?symbol=PAXGUSDT&limit=20"
    ]
    for url in endpoints:
        try:
            res = requests.get(url, timeout=4).json()
            if isinstance(res, dict) and 'bids' in res and 'asks' in res:
                total_bids = sum(float(x[1]) for x in res['bids'])
                total_asks = sum(float(x[1]) for x in res['asks'])
                if total_asks > 0:
                    skew_ratio = total_bids / total_asks
                    alpha = 1.0 if skew_ratio >= DOM_ASYMMETRY_BID_MIN else (-1.0 if skew_ratio <= DOM_ASYMMETRY_ASK_MAX else 0.0)
                    return alpha, round(skew_ratio, 2)
        except Exception:
            continue
    return 0.0, 1.0

# ================= 4. CROSS-ASSET BOND TELEMETRY =================
def evaluate_lse_bond_telemetry() -> Tuple[float, float, float, str]:
    yield_val, yield_delta = 0.0, 0.0
    impact = "NEUTRAL"
    score = 0.0

    if LSE_API_KEY:
        try:
            url = "https://api.londonstrategicedge.com/vault/series"
            headers = {"x-api-key": LSE_API_KEY}
            params = {"symbol": "US10Y", "limit": 6, "order": "desc"}
            res = requests.get(url, headers=headers, params=params, timeout=10)

            if res.status_code == 200:
                data = res.json()
                if isinstance(data, list) and len(data) >= 2:
                    yield_val = float(data[0]["value"])
                    prev_yield = float(data[-1]["value"]) if len(data) >= 6 else float(data[1]["value"])
                    yield_delta = round(yield_val - prev_yield, 4)

                    if yield_delta < -0.05:
                        score = 2.0
                        impact = "BULLISH_TAILWIND"
                    elif yield_delta > 0.05:
                        score = -2.0
                        impact = "BEARISH_PRESSURE"
                    else:
                        score = 0.0
                        impact = "NEUTRAL"
                    return score, yield_val, yield_delta, impact
        except Exception:
            pass

    if os.path.exists(LSE_MACRO_FILE):
        try:
            df = pd.read_csv(LSE_MACRO_FILE, on_bad_lines="skip")
            if not df.empty and "us10y_yield" in df.columns:
                yield_val = float(df["us10y_yield"].iloc[-1])
                yield_delta = float(df.get("yield_delta", pd.Series([0.0])).iloc[-1])
                impact = str(df.get("gold_macro_impact", pd.Series(["NEUTRAL"])).iloc[-1])
                score = 2.0 if impact == "BULLISH_TAILWIND" else (-2.0 if impact == "BEARISH_PRESSURE" else 0.0)
                return score, yield_val, yield_delta, impact
        except Exception:
            pass

    return 0.0, yield_val, yield_delta, impact

# ================= 5. SYSTEMIC MACRO STATE AGGREGATOR =================
def compute_macro_vector() -> Tuple[float, Dict[str, Any]]:
    total_alpha = 0.0
    telemetry = {}

    lse_score, us10y_val, us10y_delta, lse_impact = evaluate_lse_bond_telemetry()
    total_alpha += lse_score
    telemetry["LSE_US10Y"] = {"yield": us10y_val, "delta": us10y_delta, "impact": lse_impact, "alpha": lse_score}

    if FRED_API_KEY:
        try:
            fred = Fred(api_key=FRED_API_KEY)
            tips = fred.get_series('DFII10').dropna()
            if len(tips) >= 6:
                d_tips = tips.iloc[-1] - tips.iloc[-6]
                s_tips = 3.0 if d_tips < -0.05 else (-3.0 if d_tips > 0.05 else 0.0)
                total_alpha += s_tips
                telemetry["TIPS"] = {"delta": round(d_tips, 2), "alpha": s_tips}
        except Exception as e:
            telemetry["FRED_Error"] = str(e)

    return total_alpha, telemetry

# ================= 6. MULTI-TIMEFRAME FRACTAL KERNEL =================
def compute_mtf_fractals(c_price: float) -> Tuple[bool, str, Dict[str, float]]:
    levels = {}
    gold_1h = fetch_twelve_data("1h", 30)
    gold_30m = fetch_twelve_data("30m", 25)

    if gold_1h is None or gold_30m is None or len(gold_1h) < 8:
        return False, "NONE", levels

    h_1h, l_1h = float(gold_1h['high'].tail(24).max()), float(gold_1h['low'].tail(24).min())
    eq_1h = round((h_1h + l_1h) / 2.0, 2)
    levels["1H_50"] = eq_1h

    if abs(c_price - l_1h) <= 5.0:
        return True, "BULLISH_EXHAUSTION", levels
    elif abs(c_price - h_1h) <= 5.0:
        return True, "BEARISH_EXHAUSTION", levels

    return False, "NONE", levels

# ================= 7. VOLATILITY ESTIMATOR & RISK BUDGET =================
def compute_risk_envelope() -> Tuple[float, float, float, float, float, float]:
    df_1h = fetch_twelve_data("1h", 35)
    df_1d = fetch_twelve_data("1d", 30)

    fallback_price = 4300.0
    ema_50_1h = 4300.0
    atr_val = 8.50

    if df_1h is not None and len(df_1h) >= 15:
        c_1h = df_1h['close']
        fallback_price = float(c_1h.iloc[-1])
        ema_50_1h = float(c_1h.ewm(span=50, adjust=False).mean().iloc[-1])
        rolling_atr = (df_1h['high'] - df_1h['low']).rolling(VOLATILITY_LOOKBACK).mean().iloc[-1]
        atr_val = float(rolling_atr) if not np.isnan(rolling_atr) and rolling_atr > 0 else 8.50

    ema_20_1d = 4300.0
    pdh, pdl = 4350.0, 4250.0
    if df_1d is not None and len(df_1d) >= 20:
        c_1d = df_1d['close']
        ema_20_1d = float(c_1d.ewm(span=20, adjust=False).mean().iloc[-1])
        pdh = float(df_1d['high'].iloc[-2])
        pdl = float(df_1d['low'].iloc[-2])

    return fallback_price, ema_50_1h, ema_20_1d, pdh, pdl, round(atr_val, 2)

# ================= 8. TELEGRAM TELEMETRY BROADCASTERS =================
def dispatch_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG WARN] Credentials missing.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"[TG ERROR] {e}")
        return False

def broadcast_execution_card(side: str, spot: float, sl: float, tp: float, regime_tag: str, call_wall="N/A", put_wall="N/A", net_dex="N/A") -> bool:
    risk = abs(spot - sl)
    reward = abs(tp - spot)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    is_blast = "GAMMA_BLAST" in regime_tag or "GAMMA_COLLAPSE" in regime_tag
    icon = "🚀🟢 <b>INSTITUTIONAL GAMMA BLAST (LONG)</b>" if (is_blast and side == "BUY") else (
           "🚀🔴 <b>INSTITUTIONAL GAMMA COLLAPSE (SHORT)</b>" if is_blast else (
           "⚡🟢 <b>QUANT ALPHA LONG EXECUTION</b>" if side == "BUY" else "⚡🔴 <b>QUANT ALPHA SHORT EXECUTION</b>"))

    card = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 <b>Model</b>: <code>{regime_tag}</code>\n"
        f"💵 <b>Entry Spot</b>: <code>${spot:.2f}</code>\n"
        f"📐 <b>Profile</b>: <code>Risk: ${risk:.2f} | Target: ${reward:.2f} (1:{rr})</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 <b>Structural SL</b>: <code>${sl:.2f}</code>\n"
        f"🎯 <b>Liquidity Target</b>: <code>${tp:.2f}</code>\n"
        f"🛡️ <b>Call Wall</b>: <code>${call_wall}</code>\n"
        f"🛡️ <b>Put Wall</b>: <code>${put_wall}</code>\n"
        f"⚖️ <b>Net Delta (DEX)</b>: <code>{net_dex}</code>\n"
        f"⏰ <b>Epoch</b>: <code>{now_utc}</code>"
    )
    return dispatch_telegram(card)

def broadcast_gatekeeper_rejection(reason: str, strategy: str, offered_rr: float, barrier="NONE") -> bool:
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    msg = (
        f"🛑 <b>GATEKEEPER REJECTION</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Strategy</b>: <code>{strategy}</code>\n"
        f"• <b>Block Reason</b>: <code>{reason}</code>\n"
        f"• <b>Offered R:R</b>: <code>1:{offered_rr:.2f}</code> (Min: 1:1.30)\n"
        f"• <b>GEX Barrier</b>: <code>{barrier}</code>\n"
        f"• <b>Timestamp</b>: <code>{now_utc}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>Verdict</b>: Capital Protected"
    )
    return dispatch_telegram(msg)

def broadcast_market_pulse(
    spot: float,
    call_wall: float,
    put_wall: float,
    gamma_flip: float,
    regime: str,
    us10y_yield: float,
    us10y_impact: str,
    net_dex: float = 0.0,
    blast_active: bool = False,
    telemetry_call: str = "Call: N/A",
    telemetry_put: str = "Put: N/A",
    is_corridor_valid: bool = True
) -> bool:
    """
    Broadcasts institutional telemetry pulse without false-blast flags.
    """
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC | %d %b %Y")

    # Only announce bypass if blast is structurally verified or price actually broke boundary
    if blast_active and (spot >= call_wall or spot <= put_wall):
        barrier_status = "🚀 <b>GAMMA BLAST REGIME</b>: High directional flow detected. Dealer walls bypassed."
    elif not is_corridor_valid:
        if spot <= put_wall:
            barrier_status = f"🔴 <b>PUT WALL BREACHED</b>: Spot below floor [Depth: {telemetry_put}]"
        else:
            barrier_status = f"🚀 <b>CALL WALL SQUEEZE</b>: Spot above ceiling [Exceed: {telemetry_call}]"
    else:
        barrier_status = f"🟢 <b>CORRIDOR CLEAR</b>: Spot inside boundaries ({telemetry_call} | {telemetry_put})"

    regime_tag = "🟩 LONG GAMMA (Mean-Reverting)" if "LONG_GAMMA" in regime else "🟥 SHORT GAMMA (Volatility Expansion)"
    macro_icon = "🟢" if us10y_impact == "BULLISH_TAILWIND" else ("🔴" if us10y_impact == "BEARISH_PRESSURE" else "⚪")
    dex_bias = "🟢 Dealer Net Long" if net_dex > 0 else ("🔴 Dealer Net Short" if net_dex < 0 else "⚪ Neutral")

    pulse_card = (
        f"📡 <b>INSTITUTIONAL MARKET RADAR PULSE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>Live Spot/Futures</b>: <code>${spot:.2f}</code>\n"
        f"🏛 <b>US10Y Yield</b>: <code>{us10y_yield:.2f}%</code> {macro_icon} <code>{us10y_impact}</code>\n"
        f"⚡ <b>Dealer Regime</b>: {regime_tag}\n"
        f"⚖️ <b>Net Delta (DEX)</b>: <code>{net_dex:+.1f}M</code> ({dex_bias})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧱 <b>Gamma Corridors</b>:\n"
        f"• <b>Call Wall (Ceiling)</b>: <code>${call_wall:.2f}</code>\n"
        f"• <b>Put Wall (Floor)</b>: <code>${put_wall:.2f}</code>\n"
        f"• <b>Gamma Neutral Flip</b>: <code>${gamma_flip:.2f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <b>Corridor Telemetry</b>:\n"
        f"{barrier_status}\n"
        f"⏰ <b>Synced</b>: <code>{now_utc}</code>"
    )
    return dispatch_telegram(pulse_card)

# ================= 9. QUANTITATIVE ARBITRATION PIPELINE =================
def execute_systematic_pipeline():
    print(f"[SIGNAL ARBITRATION] Initializing Pipeline Run at UTC {datetime.now(timezone.utc).strftime('%H:%M:%S')}...")
    
    macro_alpha, macro_telemetry = compute_macro_vector()
    dom_alpha, dom_ratio = compute_order_book_skew()
    composite_alpha = macro_alpha + dom_alpha
    print(f"[ALPHA METRICS] Macro Alpha: {macro_alpha:+.1f} | DOM Alpha: {dom_alpha:+.1f} (Skew: {dom_ratio:.2f}) | Composite: {composite_alpha:+.1f}")

    fallback_price, ema_50_1h, ema_20_1d, pdh, pdl, atr_1h = compute_risk_envelope()
    live_spot = get_live_gold_spot()
    spot = live_spot if live_spot is not None else fallback_price
    print(f"[SPOT RESOLUTION] Active Spot Price: ${spot:.2f} (ATR-1H: ${atr_1h:.2f})")

    confluence_active, conf_type, matrix = compute_mtf_fractals(spot)
    weekday = datetime.now(timezone.utc).weekday()
    is_weekend = weekday >= 5

    # 1. Pull Institutional Options GEX & DEX Boundaries
    gex_data = GoldGEXEngine.read_cached_levels()
    call_wall = float(gex_data.get("call_wall_xau", spot + 50.0))
    put_wall = float(gex_data.get("put_wall_xau", spot - 50.0))
    net_dex = float(gex_data.get("net_dex_m", 0.0))
    gamma_blast_allowed = bool(gex_data.get("gamma_blast_active", False))
    gex_cushion = max(0.5 * atr_1h, 3.0)

    # Basis Normalization (Handles Futures/Spot Spread)
    cached_gex_spot = float(gex_data.get("spot_xau", spot))
    basis_gap = cached_gex_spot - spot if abs(cached_gex_spot - spot) > 10.0 else 0.0
    norm_call_wall = round(call_wall - basis_gap, 2)
    norm_put_wall = round(put_wall - basis_gap, 2)

    telemetry_call = gex_data.get("call_distance_telemetry", f"Call: +${abs(norm_call_wall - spot):.2f}")
    telemetry_put = gex_data.get("put_distance_telemetry", f"Put: +${abs(spot - norm_put_wall):.2f}")
    is_corridor_valid = norm_put_wall < spot < norm_call_wall

    print(f"[GEX STATUS] Call: ${norm_call_wall:.2f} | Put: ${norm_put_wall:.2f} | DEX: {net_dex:+.1f}M | Basis Adj: -${basis_gap:.2f}")

    if is_weekend:
        print("[PIPELINE STATUS] Market is CLOSED for the weekend.")
        return

    signal = "NEUTRAL"
    sl, tp = None, None
    conviction = "EQUILIBRIUM"
    spread_buffer = 2.50
    risk_unit = round(atr_1h * DYNAMIC_ATR_MULTIPLIER, 2)

    # --- BUY EVALUATION GATE ---
    if composite_alpha >= ALPHA_THRESHOLD_LONG:
        target_sl = round(spot - (risk_unit + spread_buffer), 2)
        target_tp = round(max(pdh, spot + (risk_unit * 2.0) + spread_buffer), 2)
        offered_rr = (target_tp - spot) / (spot - target_sl) if (spot - target_sl) > 0 else 0.0
        near_call_wall = 0 <= (norm_call_wall - spot) <= gex_cushion

        if net_dex < -400.0 and not gamma_blast_allowed:
            broadcast_gatekeeper_rejection(f"Extreme Negative DEX ({net_dex:+.1f}M) Opposing Longs", "QUANT_LONG", offered_rr, f"${norm_call_wall:.2f}")
        elif near_call_wall and not gamma_blast_allowed:
            broadcast_gatekeeper_rejection("Approaching Call Wall Ceiling without Blast Squeeze", "QUANT_LONG", offered_rr, f"${norm_call_wall:.2f}")
        elif offered_rr < MIN_RR_THRESHOLD:
            broadcast_gatekeeper_rejection("Sub-optimal Risk:Reward Profile", "QUANT_LONG", offered_rr, "NONE")
        else:
            signal = "BUY"
            conviction = "GAMMA_BLAST_LONG_SQUEEZE" if gamma_blast_allowed else "QUANT_TREND_EXPANSION"
            sl, tp = target_sl, target_tp

    # --- SELL EVALUATION GATE ---
    elif composite_alpha <= ALPHA_THRESHOLD_SHORT:
        target_sl = round(spot + (risk_unit + spread_buffer), 2)
        target_tp = round(min(pdl, spot - (risk_unit * 2.0) - spread_buffer), 2)
        offered_rr = (spot - target_tp) / (target_sl - spot) if (target_sl - spot) > 0 else 0.0
        near_put_wall = 0 <= (spot - norm_put_wall) <= gex_cushion

        # NEW INSTITUTIONAL DEX CHECK: Block short when dealers are net long (+DEX)
        if net_dex > 20.0:
            broadcast_gatekeeper_rejection(f"Positive DEX ({net_dex:+.1f}M) Absorbing Downside Flow", "QUANT_SHORT", offered_rr, f"${norm_put_wall:.2f}")
        elif near_put_wall and not gamma_blast_allowed:
            broadcast_gatekeeper_rejection("Approaching Put Wall Floor without Gamma Collapse", "QUANT_SHORT", offered_rr, f"${norm_put_wall:.2f}")
        elif offered_rr < MIN_RR_THRESHOLD:
            broadcast_gatekeeper_rejection("Sub-optimal Risk:Reward Profile", "QUANT_SHORT", offered_rr, "NONE")
        else:
            signal = "SELL"
            conviction = "GAMMA_COLLAPSE_SHORT" if gamma_blast_allowed else "QUANT_TREND_PULLBACK"
            sl, tp = target_sl, target_tp

    # --- DISPATCH SIGNAL IF QUALIFIED ---
    if signal in ["BUY", "SELL"] and sl and tp:
        print(f"[ACTION TRIGGERED] Firing {signal} signal into Telegram...")
        broadcast_execution_card(signal, spot, sl, tp, conviction, str(norm_call_wall), str(norm_put_wall), f"{net_dex:+.1f}M")
        
        # Append into trade_log.csv
        log_entry = pd.DataFrame([{
            "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "Signal": signal,
            "Price": spot,
            "SL": sl,
            "TP": tp,
            "Model": conviction
        }])
        if not os.path.exists(TRADE_LOG_FILE):
            log_entry.to_csv(TRADE_LOG_FILE, index=False)
        else:
            log_entry.to_csv(TRADE_LOG_FILE, mode='a', header=False, index=False)
        else:
        print("[PIPELINE EQUILIBRIUM] No trade qualified. State: Neutral / Corridor Range.")

# ================= 10. MAIN RUNNER ENTRYPOINT =================
if __name__ == "__main__":
    execute_systematic_pipeline()

     
