"""
order_flow_dom_engine.py
Institutional L2/L3 Microstructure Depth-of-Market (DOM) Proxy Layer
- Source 1 (Pricing Core): London Strategic Edge (LSE) Institutional Endpoint
- Source 2 (Microstructure Volume): COMEX Gold Futures (GC=F) via yfinance
- Telemetry: Dynamic Imbalance Ratio, Bid/Offer Absorption, Dynamic ATR Buffer
"""

import os
import math
import requests
import pandas as pd
import numpy as np
import yfinance as yf

LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()


# =====================================================================
# 1. LSE INSTITUTIONAL SPOT / QUOTE INGESTION
# =====================================================================
def fetch_lse_live_quote() -> dict:
    """
    Fetches real-time institutional L1 quote from London Strategic Edge.
    """
    if not LSE_API_KEY:
        return {"price": 0.0, "status": "NO_API_KEY"}

    try:
        url = "https://api.londonstrategicedge.com/v1/quotes/XAUUSD"
        headers = {
            "X-LSE-API-KEY": LSE_API_KEY,
            "Accept": "application/json"
        }
        res = requests.get(url, headers=headers, timeout=6)
        if res.status_code == 200:
            data = res.json()
            price = float(data.get("price") or data.get("spot") or 0.0)
            bid = float(data.get("bid", price - 0.25))
            ask = float(data.get("ask", price + 0.25))
            return {
                "price": round(price, 2),
                "bid": round(bid, 2),
                "ask": round(ask, 2),
                "spread": round(ask - bid, 2),
                "status": "AUTHENTICATED_LSE"
            }
    except Exception as e:
        pass

    return {"price": 0.0, "status": "LSE_FEED_ERROR"}


# =====================================================================
# 2. YFINANCE COMEX OHLCV DATA ENGINE
# =====================================================================
def fetch_comex_order_flow_bars(lookback: int = 15) -> pd.DataFrame:
    """
    Pulls 5-minute COMEX GC=F intraday bars with traded contract volume.
    """
    try:
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period="1d", interval="5m")
        if df.empty or len(df) < 5:
            # Fallback to GLD intraday if COMEX futures are paused
            gld = yf.Ticker("GLD")
            df_gld = gld.history(period="1d", interval="5m")
            if not df_gld.empty:
                return df_gld.tail(lookback)
            return pd.DataFrame()
        return df.tail(lookback)
    except Exception:
        return pd.DataFrame()


# =====================================================================
# 3. L2/L3 DOM PROXY & MICRO-IMBALANCE ANALYSIS
# =====================================================================
def analyze_l2_l3_microstructure(df: pd.DataFrame, lse_spot: float = 0.0) -> dict:
    """
    Computes Order Book Absorption, Volume Imbalance Ratio, and Liquidity Pressures.
    """
    if df.empty or len(df) < 3:
        return {
            "dom_bias": "NEUTRAL",
            "imbalance_ratio": 1.0,
            "net_delta_pressure": "BALANCED_EQUILIBRIUM",
            "atr_buffer": 8.50,
            "bid_volume": 0.0,
            "offer_volume": 0.0
        }

    # 1. Compute Dynamic 14-period ATR Volatility
    high = df["High"]
    low = df["Low"]
    close_prev = df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low - close_prev).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(window=min(len(df), 14)).mean().iloc[-1]
    atr_val = 8.50 if (math.isnan(atr) or atr <= 0) else round(float(atr * 1.25), 2)
    atr_buffer = max(4.50, min(atr_val, 18.00))

    # 2. Decomposition of Last 3 Bars (Wick Rejection & Volume Delta)
    recent_bars = df.tail(3)
    total_bid_vol = 0.0
    total_offer_vol = 0.0

    for _, row in recent_bars.iterrows():
        o, h, l, c, v = row["Open"], row["High"], row["Low"], row["Close"], row["Volume"]
        rng = h - l
        if rng <= 0 or v <= 0:
            continue

        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        body = abs(c - o)

        # Volume allocation based on auction theory
        if c >= o:
            bid_part = lower_wick + (0.75 * body)
            offer_part = upper_wick + (0.25 * body)
        else:
            bid_part = lower_wick + (0.25 * body)
            offer_part = upper_wick + (0.75 * body)

        total_bid_vol += (bid_part / rng) * v
        total_offer_vol += (offer_part / rng) * v

    # Imbalance Ratio Calculation
    if total_offer_vol <= 0:
        imbalance_ratio = 3.0
    else:
        imbalance_ratio = round(total_bid_vol / total_offer_vol, 2)

    # 3. Institutional Thresholds
    if imbalance_ratio >= 1.60:
        dom_bias = "INSTITUTIONAL_BID_ABSORPTION"
        pressure = "AGGRESSIVE_BUYING_FLOW"
    elif imbalance_ratio <= 0.62:
        dom_bias = "INSTITUTIONAL_OFFER_ABSORPTION"
        pressure = "AGGRESSIVE_SELLING_FLOW"
    else:
        dom_bias = "TWO_WAY_AUCTION_BALANCED"
        pressure = "EQUILIBRIUM"

    return {
        "dom_bias": dom_bias,
        "imbalance_ratio": imbalance_ratio,
        "net_delta_pressure": pressure,
        "atr_buffer": atr_buffer,
        "bid_volume": round(total_bid_vol, 1),
        "offer_volume": round(total_offer_vol, 1)
    }


# =====================================================================
# 4. MASTER ENGINE RUNNER
# =====================================================================
def get_order_flow_telemetry() -> dict:
    """
    Main callable function combining LSE live quote and COMEX L2/L3 volume proxy.
    """
    lse_quote = fetch_lse_live_quote()
    lse_price = lse_quote.get("price", 0.0)

    df_comex = fetch_comex_order_flow_bars()
    dom_metrics = analyze_l2_l3_microstructure(df_comex, lse_spot=lse_price)

    return {
        "lse_feed": lse_quote,
        "dom_metrics": dom_metrics
    }


if __name__ == "__main__":
    data = get_order_flow_telemetry()
    print("--- L2/L3 ORDER FLOW TELEMETRY ---")
    print(f"LSE Spot: ${data['lse_feed'].get('price', 0.0)} | Status: {data['lse_feed'].get('status')}")
    print(f"DOM Bias: {data['dom_metrics']['dom_bias']}")
    print(f"Imbalance Ratio: {data['dom_metrics']['imbalance_ratio']}")
    print(f"Adaptive ATR Buffer: ${data['dom_metrics']['atr_buffer']}")
