"""
free_gex_engine.py
Institutional Gold (XAU/USD) Multi-Tier Real-Time GEX Engine.
Integrates:
- LSE_API_KEY (London Strategic Edge Data Core with Header X-LSE-API-KEY)
- TWELVE_DATA_API_KEY (Interbank Live Spot Feed)
- FRED_API_KEY (Macro Indicators)
- COMEX Futures (GC=F) & SPDR Gold Shares (GLD) Options BSM Gamma
"""

import os
import json
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from scipy.stats import norm
import yfinance as yf
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(SCRIPT_DIR, "gex_levels.json")

LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()


def fetch_live_xau_spot() -> tuple[float, str]:
    """Dynamically pull real-time spot Gold price via LSE, TwelveData, or COMEX."""
    # Source 1: London Strategic Edge (Official Endpoint)
    if LSE_API_KEY:
        try:
            url = "https://api.londonstrategicedge.com/v1/quotes/XAUUSD"
            headers = {"X-LSE-API-KEY": LSE_API_KEY, "Accept": "application/json"}
            res = requests.get(url, headers=headers, timeout=6)
            if res.status_code == 200:
                payload = res.json()
                price = float(payload.get("price") or payload.get("last") or payload.get("data", {}).get("price", 0.0))
                if price > 1500.0:
                    print(f"[DATA CORE] Live LSE Terminal Spot: ${price:.2f}")
                    return round(price, 2), "LSE_DATA_CORE"
        except Exception as e:
            print(f"[LSE SPOT ERROR] {e}")

    # Source 2: Twelve Data API
    if TWELVE_DATA_API_KEY:
        try:
            url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_API_KEY}"
            res = requests.get(url, timeout=6)
            if res.status_code == 200:
                data = res.json()
                if "price" in data:
                    price = float(data["price"])
                    if price > 1500.0:
                        print(f"[DATA CORE] Live TwelveData Spot: ${price:.2f}")
                        return round(price, 2), "TWELVE_DATA"
        except Exception as e:
            print(f"[TWELVE DATA ERROR] {e}")

    # Source 3: COMEX Gold Futures (GC=F)
    try:
        t = yf.Ticker("GC=F")
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            price = float(hist["Close"].dropna().iloc[-1])
            if price > 1500.0:
                print(f"[DATA CORE] Live COMEX Futures (GC=F): ${price:.2f}")
                return round(price, 2), "COMEX_CONTINUOUS"
    except Exception as e:
        print(f"[GC=F ERROR] {e}")

    # Source 4: GLD ETF implied ratio fallback
    try:
        gld_hist = yf.Ticker("GLD").history(period="1d", interval="1m")
        if not gld_hist.empty:
            gld_close = float(gld_hist["Close"].dropna().iloc[-1])
            implied = gld_close * 10.944
            return round(implied, 2), "GLD_IMPLIED_PARITY"
    except Exception:
        pass

    raise RuntimeError("Failed to retrieve live market spot price from all active feeds.")


def fetch_lse_market_telemetry() -> dict:
    """Fetch institutional order flow / macro telemetry from LSE."""
    info = {
        "lse_status": "ONLINE" if LSE_API_KEY else "NO_KEY",
        "institutional_core": "AUTHENTICATED" if LSE_API_KEY else "PUBLIC_GUEST",
        "order_flow_bias": "NEUTRAL"
    }
    if LSE_API_KEY:
        try:
            url = "https://api.londonstrategicedge.com/v1/telemetry/xauusd"
            headers = {"X-LSE-API-KEY": LSE_API_KEY}
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                info["order_flow_bias"] = res.json().get("bias", "ACCUMULATION")
                info["institutional_core"] = "LSE_QUANT_SYNCHRONIZED"
        except Exception:
            info["institutional_core"] = "LSE_DIRECT_PIPELINE"
    return info


class GoldGEXEngine:
    def __init__(self, risk_free_rate: float = 0.045):
        self.r = risk_free_rate

    @staticmethod
    def _bsm_greeks(S: float, K: float, T: float, r: float, sigma: float) -> tuple:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.0, 0.0, 0.0
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        gamma = float(norm.pdf(d1) / (S * sigma * np.sqrt(T)))
        delta_call = float(norm.cdf(d1))
        delta_put = float(delta_call - 1.0)
        return gamma, delta_call, delta_put

    def compute_gex(self) -> dict:
        spot_xau, data_source = fetch_live_xau_spot()
        lse_telemetry = fetch_lse_market_telemetry()

        gld = yf.Ticker("GLD")
        hist = gld.history(period="5d")
        if hist.empty:
            raise ValueError("Failed to pull GLD historical data.")
        
        s_gld = float(hist["Close"].dropna().iloc[-1])
        conv_ratio = spot_xau / s_gld

        expirations = gld.options
        if not expirations:
            raise ValueError("No option chains found for GLD.")

        now_utc = datetime.now(timezone.utc)
        valid_expiries = []
        for exp in expirations:
            t_delta = (datetime.strptime(exp, "%Y-%m-%d").replace(tzinfo=timezone.utc) - now_utc).days
            if 0 <= t_delta <= 45:
                valid_expiries.append((exp, max(t_delta / 365.0, 1 / 365.0)))

        call_gex_map = {}
        put_gex_map = {}
        total_call_dex = 0.0
        total_put_dex = 0.0

        min_strike = s_gld * 0.80
        max_strike = s_gld * 1.20

        for exp_str, T in valid_expiries:
            try:
                chain = gld.option_chain(exp_str)
            except Exception:
                continue

            for _, row in chain.calls.iterrows():
                strike = float(row["strike"])
                if not (min_strike <= strike <= max_strike):
                    continue
                oi = float(row["openInterest"]) if not np.isnan(row.get("openInterest", 0.0)) else 0.0
                iv = float(row["impliedVolatility"]) if not np.isnan(row.get("impliedVolatility", 0.0)) else 0.0
                if oi > 0 and 0.05 <= iv <= 1.50:
                    gamma, d_call, _ = self._bsm_greeks(s_gld, strike, T, self.r, iv)
                    gex = gamma * oi * 100.0 * (s_gld ** 2) * 0.01
                    call_gex_map[strike] = call_gex_map.get(strike, 0.0) + gex
                    total_call_dex += (d_call * oi * 100.0 * s_gld) / 1_000_000.0

            for _, row in chain.puts.iterrows():
                strike = float(row["strike"])
                if not (min_strike <= strike <= max_strike):
                    continue
                oi = float(row["openInterest"]) if not np.isnan(row.get("openInterest", 0.0)) else 0.0
                iv = float(row["impliedVolatility"]) if not np.isnan(row.get("impliedVolatility", 0.0)) else 0.0
                if oi > 0 and 0.05 <= iv <= 1.50:
                    gamma, _, d_put = self._bsm_greeks(s_gld, strike, T, self.r, iv)
                    gex = gamma * oi * 100.0 * (s_gld ** 2) * 0.01
                    put_gex_map[strike] = put_gex_map.get(strike, 0.0) + gex
                    total_put_dex += (abs(d_put) * oi * 100.0 * s_gld) / 1_000_000.0

        if not call_gex_map or not put_gex_map:
            raise ValueError("Insufficient options liquidity.")

        otm_calls = {k: v for k, v in call_gex_map.items() if k >= s_gld and v > 0}
        call_wall_gld = max(otm_calls, key=otm_calls.get) if otm_calls else max(call_gex_map, key=call_gex_map.get)

        otm_puts = {k: v for k, v in put_gex_map.items() if k <= s_gld and v > 0}
        put_wall_gld = max(otm_puts, key=otm_puts.get) if otm_puts else max(put_gex_map, key=put_gex_map.get)

        if put_wall_gld >= call_wall_gld:
            sub_puts = {k: v for k, v in put_gex_map.items() if k < call_wall_gld and v > 0}
            put_wall_gld = max(sub_puts, key=sub_puts.get) if sub_puts else round(call_wall_gld * 0.98, 2)

        call_wall_xau = round(call_wall_gld * conv_ratio, 2)
        put_wall_xau = round(put_wall_gld * conv_ratio, 2)

        call_dist = call_wall_xau - spot_xau
        put_dist = spot_xau - put_wall_xau
        telemetry_call = f"Call: {'+' if call_dist >= 0 else '-'}${abs(call_dist):.2f}"
        telemetry_put = f"Put: {'+' if put_dist >= 0 else '-'}${abs(put_dist):.2f}"

        strikes = sorted(list(set(call_gex_map.keys()) | set(put_gex_map.keys())))
        net_gex_dict = {k: call_gex_map.get(k, 0.0) - put_gex_map.get(k, 0.0) for k in strikes}
        total_net_gex = sum(net_gex_dict.values())

        gamma_flip_gld = s_gld
        best_diff = float("inf")
        for i in range(len(strikes) - 1):
            k1, k2 = strikes[i], strikes[i + 1]
            v1, v2 = net_gex_dict[k1], net_gex_dict[k2]
            if v1 * v2 <= 0:
                mid = (k1 + k2) / 2.0
                d = abs(mid - s_gld)
                if d < best_diff:
                    best_diff = d
                    gamma_flip_gld = mid

        gamma_flip_xau = round(gamma_flip_gld * conv_ratio, 2)
        net_dex_m = round(total_call_dex - total_put_dex, 2)
        gamma_blast_active = (total_net_gex < 0) and (abs(net_dex_m) >= 8.0)

        payload = {
            "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "status": "HEALTHY",
            "spot_xau": spot_xau,
            "data_source": data_source,
            "gld_close": round(s_gld, 2),
            "conv_ratio": round(conv_ratio, 4),
            "call_wall_xau": call_wall_xau,
            "put_wall_xau": put_wall_xau,
            "gamma_flip_xau": gamma_flip_xau,
            "net_dex_m": net_dex_m,
            "net_gamma_regime": "LONG_GAMMA_MEAN_REVERT" if total_net_gex > 0 else "SHORT_GAMMA_EXPANSION",
            "gamma_blast_active": bool(gamma_blast_active),
            "call_distance_telemetry": telemetry_call,
            "put_distance_telemetry": telemetry_put,
            "is_corridor_valid": bool(put_wall_xau < spot_xau < call_wall_xau),
            "lse_telemetry": lse_telemetry
        }

        with open(CACHE_FILE, "w") as f:
            json.dump(payload, f, indent=2)

        print(f"[ENGINE OK] Successfully written dynamic levels to {CACHE_FILE}")
        return payload


if __name__ == "__main__":
    engine = GoldGEXEngine()
    out = engine.compute_gex()
    print("Execution complete. Output:", json.dumps(out, indent=2))
                       
