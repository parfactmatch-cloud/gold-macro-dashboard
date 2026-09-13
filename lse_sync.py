"""
LSE Isolated Macro & Options GEX Sync Engine
- Fetches US10Y Bond Yields from London Strategic Edge API -> saves to 'lse_macro.csv'
- Computes SPDR Gold Shares (GLD) dealer Gamma Walls & Flip -> saves to 'gex_levels.json'
- Multi-Source Spot Feed: CME Futures (GC=F) -> Stooq XAUUSD -> Binance PAXGUSDT -> GLD NAV
- Regime-agnostic: Directly supports CME GC1! $4,400+ pricing without static baseline truncations
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
    """
    Regime-agnostic Spot & Futures Gold resolution.
    Directly aligns with CME GC1! ($4,400+ regime) and Spot XAU/USD.
    """
    # 1. Primary: CME Continuous Gold Futures (GC=F) with Browser Session & 7-day Buffer
    try:
        import yfinance as yf
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"
        })
        ticker = yf.Ticker("GC=F", session=session)
        hist = ticker.history(period="7d")
        if not hist.empty:
            price = float(hist["Close"].dropna().iloc[-1])
            if price > 1500.0:
                print(f"[SPOT SUCCESS] Sourced via CME Futures (GC=F): ${price:.2f}")
                return price
    except Exception as e:
        print(f"[SPOT CME SKIP] {e}")

    # 2. Secondary: Stooq Institutional Spot Gold (XAUUSD)
    try:
        stooq_url = "https://stooq.com/q/l/?s=xauusd&f=sd2t2ohlcv&h&e=csv"
        res = requests.get(stooq_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if res.status_code == 200:
            lines = res.text.strip().split("\n")
            if len(lines) >= 2:
                cols = lines[1].split(",")
                close_val = float(cols[6])
                if close_val > 1500.0:
                    print(f"[SPOT SUCCESS] Sourced via Stooq XAUUSD: ${close_val:.2f}")
                    return close_val
    except Exception as e:
        print(f"[SPOT STOOQ SKIP] {e}")

    # 3. Tertiary: Binance PAXGUSDT (24/7 continuous order tape)
    endpoints = [
        "https://api.binance.com/api/v3/ticker/price?symbol=PAXGUSDT",
        "https://data-api.binance.vision/api/v3/ticker/price?symbol=PAXGUSDT"
    ]
    for url in endpoints:
        try:
            res = requests.get(url, timeout=4)
            if res.status_code == 200:
                price = float(res.json().get("price", 0.0))
                if price > 1500.0:
                    print(f"[SPOT SUCCESS] Sourced via Binance PAXG: ${price:.2f}")
                    return price
        except Exception:
            continue

    # 4. Fallback: SPDR Gold Shares (GLD) Dynamic Basket Translation
    try:
        import yfinance as yf
        gld_hist = yf.Ticker("GLD").history(period="7d")
        if not gld_hist.empty:
            gld_close = float(gld_hist["Close"].dropna().iloc[-1])
            derived = round(gld_close * 10.82, 2)
            if derived > 1500.0:
                print(f"[SPOT SUCCESS] Derived via GLD Basket: ${derived:.2f}")
                return derived
    except Exception as e:
        print(f"[SPOT GLD SKIP] {e}")

    raise RuntimeError("CRITICAL: All gold price feeds unreachable.")

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
    
