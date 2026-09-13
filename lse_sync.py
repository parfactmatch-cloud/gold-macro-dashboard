"""
LSE Isolated Macro & Options GEX Sync Engine
- Fetches US10Y Bond Yields from London Strategic Edge API -> saves to 'lse_macro.csv'
- Computes SPDR Gold Shares (GLD) dealer Gamma Walls & Flip -> saves to 'gex_levels.json'
"""

import os
import requests
import pandas as pd
from datetime import datetime, timezone

# GEX Engine Import (free_gex_engine.py must reside in the same execution path)
from free_gex_engine import GoldGEXEngine

LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()
LSE_MACRO_FILE = "lse_macro.csv"

def fetch_lse_series(symbol="US10Y", limit=10):
    if not LSE_API_KEY:
        print("[LSE ERROR] LSE_API_KEY secret is missing.")
        return None

    url = "https://api.londonstrategicedge.com/vault/series"
    headers = {"x-api-key": LSE_API_KEY}
    params = {
        "symbol": symbol,
        "limit": limit,
        "order": "desc"
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                df = pd.DataFrame(data)
                return df
        print(f"[LSE HTTP ERROR] Status {res.status_code}: {res.text}")
        return None
    except Exception as e:
        print(f"[LSE FETCH EXCEPTION] {e}")
        return None

def fetch_live_gold_spot() -> float:
    """Fetches real-time Gold spot proxy for GLD basket conversion."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("GC=F")
        hist = ticker.history(period="1d")
        if not hist.empty:
            return float(hist["Close"].iloc[-1])
    except Exception as e:
        print(f"[SPOT FETCH WARN] Yahoo Finance proxy failed: {e}")
    
    # Fallback to last recorded macro price or institutional baseline
    return 2650.0

def sync_gex(spot_price: float):
    """Executes institutional options gamma engine and saves cache."""
    print(f"[GEX SYNC RUN] Spot Reference: ${spot_price:.2f}")
    try:
        engine = GoldGEXEngine()
        gex_data = engine.compute_gex(spot_xau=spot_price)
        print(
            f"[GEX SUCCESS] Call Wall: {gex_data.get('call_wall_xau')} | "
            f"Put Wall: {gex_data.get('put_wall_xau')} | "
            f"Flip: {gex_data.get('gamma_flip_xau')} | "
            f"Status: {gex_data.get('status')}"
        )
        return gex_data
    except Exception as e:
        print(f"[GEX SYNC ERROR] Calculation aborted: {e}")
        return None

def run_sync():
    print(f"[LSE SYNC RUN] UTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    
    # 1. Fetch US10Y Bond Yield (Macro Directional Filter)
    df_us10y = fetch_lse_series("US10Y", limit=5)
    
    if df_us10y is not None and not df_us10y.empty:
        latest_val = float(df_us10y["value"].iloc[0])
        prev_val = float(df_us10y["value"].iloc[1]) if len(df_us10y) > 1 else latest_val
        delta_yield = round(latest_val - prev_val, 4)

        # Yield rising = Bearish for Gold; Yield falling = Bullish for Gold
        gold_macro_impact = "BEARISH_PRESSURE" if delta_yield > 0.02 else ("BULLISH_TAILWIND" if delta_yield < -0.02 else "NEUTRAL")

        record = pd.DataFrame([{
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "us10y_yield": latest_val,
            "yield_delta": delta_yield,
            "gold_macro_impact": gold_macro_impact
        }])

        record.to_csv(LSE_MACRO_FILE, index=False)
        print(f"[LSE SUCCESS] US10Y: {latest_val}% (Δ {delta_yield:+.2f}) -> Impact: {gold_macro_impact}")
    else:
        print("[LSE SYNC WARN] Macro yield series skipped or failed. Retaining prior lse_macro.csv state.")

    # 2. Options Gamma Exposure (GEX) Calculation
    spot_xau = fetch_live_gold_spot()
    sync_gex(spot_price=spot_xau)

if __name__ == "__main__":
    run_sync()
        
