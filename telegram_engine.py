"""
===============================================================================
SYSTEM: INSTITUTIONAL SYSTEMATIC MACRO & MTF CONFLUENCE ENGINE (XAU/USD)
METHODOLOGY: CROSS-ASSET TELEMETRY, FRACTAL EQUILIBRIUM & ASYMMETRIC DOM SKEW
ROLE: PRODUCTION QUANT EXECUTION ENGINE
===============================================================================
"""

import os
import requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone
from fredapi import Fred
from typing import Dict, Tuple, Optional, Any

# ================= 1. SYSTEM PARAMETERS & CONFIGURATION =================
FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()

TRADE_LOG_FILE = "trade_log.csv"
LSE_MACRO_FILE = "lse_macro.csv"

# Systematic Decision Hyperparameters
ALPHA_THRESHOLD_LONG = 4.0
ALPHA_THRESHOLD_SHORT = -4.0
DOM_ASYMMETRY_BID_MIN = 1.25
DOM_ASYMMETRY_ASK_MAX = 0.80
VOLATILITY_LOOKBACK = 14
DYNAMIC_ATR_MULTIPLIER = 1.25
CONFLUENCE_EQUILIBRIUM_TOLERANCE = 3.0

# ================= 2. DATA INGESTION MATRIX =================
def fetch_twelve_data(interval: str = "1h", outputsize: int = 30) -> Optional[pd.DataFrame]:
    """
    Ingests and validates standard OHLCV time-series via Twelve Data REST API.
    """
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
    Fetches millisecond-level live gold spot via Binance PAXGUSDT order tape.
    """
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
    """
    Evaluates institutional book imbalance:
    Skew = sum(Bids) / sum(Asks) within the top 20 Level-2 depth layers.
    """
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

# ================= 4. CROSS-ASSET BOND TELEMETRY (LSE ENGINE) =================
def evaluate_lse_bond_telemetry() -> Tuple[float, float, float, str]:
    """
    Computes bond yield velocity: v_yield = Y(t) - Y(t-k)
    Normalizes rate pressure into an institutional alpha score.
    """
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

    # Fallback to local persistent cache
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
    """
    Aggregates orthogonal macro predictors:
    Macro Alpha = S_tips + S_fed_liq + S_dxy + S_lse_yield
    """
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

            walcl = fred.get_series('WALCL').dropna()
            tga = fred.get_series('WTREGEN').dropna()
            rrp = fred.get_series('RRPONTSYD').dropna()
            if len(walcl) >= 2 and len(tga) >= 2 and len(rrp) >= 2:
                liq_curr = walcl.iloc[-1] - tga.iloc[-1] - rrp.iloc[-1]
                liq_prev = walcl.iloc[-2] - tga.iloc[-2] - rrp.iloc[-2]
                s_liq = 2.0 if (liq_curr - liq_prev) > 0 else -2.0
                total_alpha += s_liq
                telemetry["FedLiquidity"] = {"alpha": s_liq}
        except Exception as e:
            telemetry["Macro_Error"] = str(e)

    try:
        dxy_df = fetch_twelve_data("1d", 10)
        if dxy_df is not None and len(dxy_df) >= 6:
            d_dxy = float(dxy_df['close'].iloc[-1] - dxy_df['close'].iloc[-6])
            s_dxy = 2.0 if d_dxy < -0.50 else (-2.0 if d_dxy > 0.50 else 0.0)
            total_alpha += s_dxy
            telemetry["DXY"] = {"delta": round(d_dxy, 2), "alpha": s_dxy}
    except Exception:
        pass

    return total_alpha, telemetry

# ================= 6. MULTI-TIMEFRAME FRACTAL KERNEL =================
def compute_mtf_fractals(c_price: float) -> Tuple[bool, str, Dict[str, float]]:
    levels = {}
    gold_1h = fetch_twelve_data("1h", 30)
    gold_30m = fetch_twelve_data("30m", 25)
    gold_15m = fetch_twelve_data("15m", 20)
    gold_5m = fetch_twelve_data("5m", 20)

    if any(x is None or len(x) < 8 for x in [gold_1h, gold_30m, gold_15m, gold_5m]):
        return False, "NONE", levels

    # 50% Mean Reversion Coordinates
    h_1h, l_1h = float(gold_1h['high'].tail(24).max()), float(gold_1h['low'].tail(24).min())
    eq_1h = round((h_1h + l_1h) / 2.0, 2)

    h_30m, l_30m = float(gold_30m['high'].tail(16).max()), float(gold_30m['low'].tail(16).min())
    eq_30m = round((h_30m + l_30m) / 2.0, 2)

    levels["1H_50"] = eq_1h
    levels["30M_50"] = eq_30m

    # Micro Tail-Risk Exhaustion Bounds (Asymmetric 4X Range Expansion)
    r_15m = max(float(abs(gold_15m['high'].iloc[-4] - gold_15m['low'].iloc[-4])), 2.0)
    levels["15M_4X_Down"] = round(h_1h - (r_15m * 4.0), 2)
    levels["15M_4X_Up"] = round(l_1h + (r_15m * 4.0), 2)

    r_5m = max(float(abs(gold_5m['high'].iloc[-6] - gold_5m['low'].iloc[-6])), 1.0)
    levels["5M_4X_Down"] = round(h_1h - (r_5m * 4.0), 2)
    levels["5M_4X_Up"] = round(l_1h + (r_5m * 4.0), 2)

    # Confluence Discrimination
    bullish_exhaustion = (
        (abs(eq_1h - levels["15M_4X_Down"]) <= 3.5 or abs(eq_1h - levels["5M_4X_Down"]) <= 2.5 or abs(eq_30m - levels["5M_4X_Down"]) <= 2.0)
        and (abs(c_price - eq_1h) <= CONFLUENCE_EQUILIBRIUM_TOLERANCE or abs(c_price - eq_30m) <= 2.0)
    )

    bearish_exhaustion = (
        (abs(eq_1h - levels["15M_4X_Up"]) <= 3.5 or abs(eq_1h - levels["5M_4X_Up"]) <= 2.5 or abs(eq_30m - levels["5M_4X_Up"]) <= 2.0)
        and (abs(c_price - eq_1h) <= CONFLUENCE_EQUILIBRIUM_TOLERANCE or abs(c_price - eq_30m) <= 2.0)
    )

    if bullish_exhaustion:
        return True, "BULLISH_EXHAUSTION", levels
    elif bearish_exhaustion:
        return True, "BEARISH_EXHAUSTION", levels

    return False, "NONE", levels

# ================= 7. VOLATILITY ESTIMATOR & RISK BUDGET =================
def compute_risk_envelope() -> Tuple[float, float, float, float, float, float]:
    df_1h = fetch_twelve_data("1h", 35)
    df_1d = fetch_twelve_data("1d", 30)

    fallback_price = 2650.0
    ema_50_1h = 2650.0
    atr_val = 8.50

    if df_1h is not None and len(df_1h) >= 15:
        c_1h = df_1h['close']
        fallback_price = float(c_1h.iloc[-1])
        ema_50_1h = float(c_1h.ewm(span=50, adjust=False).mean().iloc[-1])

        tr = pd.concat([
            df_1h['high'] - df_1h['low'],
            (df_1h['high'] - c_1h.shift(1)).abs(),
            (df_1h['low'] - c_1h.shift(1)).abs()
        ], axis=1).max(axis=1)
        rolling_atr = tr.rolling(VOLATILITY_LOOKBACK).mean().iloc[-1]
        atr_val = float(rolling_atr) if not np.isnan(rolling_atr) and rolling_atr > 0 else 8.50

    ema_20_1d = 2640.0
    pdh, pdl = 2670.0, 2630.0
    if df_1d is not None and len(df_1d) >= 20:
        c_1d = df_1d['close']
        ema_20_1d = float(c_1d.ewm(span=20, adjust=False).mean().iloc[-1])
        pdh = float(df_1d['high'].iloc[-2])
        pdl = float(df_1d['low'].iloc[-2])

    return fallback_price, ema_50_1h, ema_20_1d, pdh, pdl, round(atr_val, 2)

# ================= 8. AUDIT & DISPATCH =================
def dispatch_telegram(message: str):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"},
            timeout=10
        )

def is_duplicate_order(signal: str, price: float) -> bool:
    if not os.path.exists(TRADE_LOG_FILE):
        return False
    try:
        df = pd.read_csv(TRADE_LOG_FILE, on_bad_lines='skip')
        active = df[df['Signal'].isin(['BUY', 'SELL'])]
        if active.empty:
            return False
        last = active.iloc[-1]
        return last['Signal'] == signal and abs(float(last['Price']) - price) <= 2.0
    except Exception:
        return False

# ================= 9. QUANTITATIVE ARBITRATION PIPELINE =================
def execute_systematic_pipeline():
    macro_alpha, macro_telemetry = compute_macro_vector()
    dom_alpha, dom_ratio = compute_order_book_skew()
    composite_alpha = macro_alpha + dom_alpha

    fallback_price, ema_50_1h, ema_20_1d, pdh, pdl, atr_1h = compute_risk_envelope()
    live_spot = get_live_gold_spot()
    spot = live_spot if live_spot is not None else fallback_price

    confluence_active, conf_type, matrix = compute_mtf_fractals(spot)
    session_active = (7 <= datetime.now(timezone.utc).hour <= 18)

    signal = "NEUTRAL"
    sl, tp, be = None, None, None
    conviction = "EQUILIBRIUM"

    spread_buffer = 2.50
    risk_unit = round(atr_1h * DYNAMIC_ATR_MULTIPLIER, 2)

    # Quantitative Decision Gate
    if session_active:
        if composite_alpha >= ALPHA_THRESHOLD_LONG and spot > ema_50_1h and spot > ema_20_1d:
            if confluence_active and conf_type == "BULLISH_EXHAUSTION" and dom_ratio >= DOM_ASYMMETRY_BID_MIN:
                signal = "BUY"
                conviction = "INSTITUTIONAL QUANT CONFLUENCE"
                sl = round(spot - (risk_unit + spread_buffer), 2)
                tp = round(max(pdh, spot + (risk_unit * 2.0) + spread_buffer), 2)
                be = round(spot + risk_unit, 2)

        elif composite_alpha <= ALPHA_THRESHOLD_SHORT and spot < ema_50_1h and spot < ema_20_1d:
            if confluence_active and conf_type == "BEARISH_EXHAUSTION" and dom_ratio <= DOM_ASYMMETRY_ASK_MAX:
                signal = "SELL"
                conviction = "INSTITUTIONAL QUANT CONFLUENCE"
                sl = round(spot + (risk_unit + spread_buffer), 2)
                tp = round(min(pdl, spot - (risk_unit * 2.0) - spread_buffer), 2)
                be = round(spot - risk_unit, 2)

    is_duplicate = is_duplicate_order(signal, spot) if signal in ["BUY", "SELL"] else False

    # Persist Structured State
    payload = pd.DataFrame([{
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "Price": spot,
        "EMA_50_1H": ema_50_1h,
        "EMA_20_1D": ema_20_1d,
        "PDH": pdh,
        "PDL": pdl,
        "ATR_1H": atr_1h,
        "Composite_Alpha": composite_alpha,
        "DOM_Ratio": dom_ratio,
        "Signal": signal if not is_duplicate else "NEUTRAL_DUPLICATE_SUPPRESSED",
        "Conviction": conviction,
        "SL": sl,
        "TP": tp,
        "Breakeven": be
    }])

    if os.path.exists(TRADE_LOG_FILE):
        payload.to_csv(TRADE_LOG_FILE, mode='a', header=False, index=False)
    else:
        payload.to_csv(TRADE_LOG_FILE, index=False)

    # Telegram Signal Telemetry
    if signal in ["BUY", "SELL"] and not is_duplicate:
        icon = "🟢" if signal == "BUY" else "🔴"
        target_projection = matrix.get('5M_4X_Down', 0.0) if signal == "BUY" else matrix.get('5M_4X_Up', 0.0)
        lse_data = macro_telemetry.get("LSE_US10Y", {})
        yield_str = f"{lse_data.get('yield', 0.0):.2f}% ({lse_data.get('impact', 'NEUTRAL')})"

        card = f"""
{icon} *QUANTITATIVE ALPHA ALERT: {signal}*
⚡ *Conviction Level:* `{conviction}`

📊 *Composite Alpha Score:* `{composite_alpha:+0.1f}` | *DOM Skew:* `{dom_ratio:.2f}`
🏛 *LSE US10Y Telemetry:* `{yield_str}`
📈 *Execution Spot:* `${spot:.2f}`
🏛 *Systemic Trend Filters:* `1H EMA50: ${ema_50_1h:.2f}` | `1D EMA20: ${ema_20_1d:.2f}`
🎯 *PDH:* `${pdh:.2f}` | *PDL:* `${pdl:.2f}` | *1H ATR:* `${atr_1h:.2f}`

🏛 *Fractal Equilibrium Metrics:*
• *1H 50% Mean-Reversion:* `${matrix.get('1H_50', 0.0):.2f}`
• *30M Equilibrium Band:* `${matrix.get('30M_50', 0.0):.2f}`
• *Micro 4X Projection:* `${target_projection:.2f}`
• *DOM Absorption Ratio:* `{dom_ratio:.2f}`

💼 *Parametric Risk Envelope:*
• *Entry:* `${spot:.2f}`
• *Stop Loss (1.0R):* `${sl:.2f}` (Volatility ATR-Buffered)
• *Target (2.0R):* `${tp:.2f}` (Structural Liquidity Pool)
• *Breakeven Trigger:* `${be:.2f}` (+1.0R Vector Move)

_System: Institutional Multi-Timeframe Alignment + LSE Yield Dynamics_
"""
        dispatch_telegram(card)
        print(f"[SYSTEMIC_DISPATCH] {signal} executed at ${spot:.2f} | Alpha: {composite_alpha:+0.1f}")
    else:
        print(f"[EQUILIBRIUM_STATE] Alpha: {composite_alpha:+0.1f} | Skew: {dom_ratio:.2f} | Spot: ${spot:.2f} | Gate: {'OPEN' if session_active else 'SESSION_LOCKED'}")

if __name__ == "__main__":
    execute_systematic_pipeline()
    
