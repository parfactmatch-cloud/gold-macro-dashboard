"""
LSE Isolated Macro Sync Engine
Fetches US10Y Bond Yields and Macro Indicators from London Strategic Edge API
Saves locally to 'lse_macro.csv' for modular strategy consumption.
"""

import os
import requests
import pandas as pd
from datetime import datetime, timezone

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

def run_sync():
    print(f"[LSE SYNC RUN] UTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    
    # 1. Fetch US10Y Bond Yield (Inversely correlated with Gold)
    df_us10y = fetch_lse_series("US10Y", limit=5)
    
    if df_us10y is None or df_us10y.empty:
        print("[LSE SYNC] Unable to refresh macro yield series.")
        return

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

if __name__ == "__main__":
    run_sync()

