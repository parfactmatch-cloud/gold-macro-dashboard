"""
free_gex_engine.py
Institutional SPDR Gold Shares (GLD) Options Gamma Engine.
Computes Black-Scholes Dealer GEX and projects Wall levels to Spot XAU/USD.
"""

import os
import json
from datetime import datetime, timezone
import numpy as np
from scipy.stats import norm
import yfinance as yf

CACHE_FILE = os.path.join(os.path.dirname(__file__), "gex_levels.json")

class GoldGEXEngine:
    def __init__(self, risk_free_rate: float = 0.045):
        self.r = risk_free_rate

    @staticmethod
    def _bsm_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.0
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        return float(norm.pdf(d1) / (S * sigma * np.sqrt(T)))

    def compute_gex(self, spot_xau: float) -> dict:
        """
        Fetches front options chains for GLD, computes call/put GEX,
        and translates critical dealer gamma walls into XAU/USD spot levels.
        """
        try:
            gld = yf.Ticker("GLD")
            hist = gld.history(period="1d")
            if hist.empty:
                raise ValueError("GLD quote fetch failed.")
            
            s_gld = float(hist["Close"].iloc[-1])
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

            for exp_str, T in valid_expiries:
                chain = gld.option_chain(exp_str)
                
                # Calls: Dealer Positive Gamma
                for _, row in chain.calls.iterrows():
                    strike = float(row["strike"])
                    oi = float(row["openInterest"]) if not np.isnan(row["openInterest"]) else 0.0
                    iv = float(row["impliedVolatility"]) if not np.isnan(row["impliedVolatility"]) else 0.0
                    if oi > 0 and iv > 0.01:
                        gamma = self._bsm_gamma(s_gld, strike, T, self.r, iv)
                        gex = gamma * oi * 100.0 * (s_gld ** 2) * 0.01
                        call_gex_map[strike] = call_gex_map.get(strike, 0.0) + gex

                # Puts: Dealer Negative Gamma
                for _, row in chain.puts.iterrows():
                    strike = float(row["strike"])
                    oi = float(row["openInterest"]) if not np.isnan(row["openInterest"]) else 0.0
                    iv = float(row["impliedVolatility"]) if not np.isnan(row["impliedVolatility"]) else 0.0
                    if oi > 0 and iv > 0.01:
                        gamma = self._bsm_gamma(s_gld, strike, T, self.r, iv)
                        gex = gamma * oi * 100.0 * (s_gld ** 2) * 0.01
                        put_gex_map[strike] = put_gex_map.get(strike, 0.0) - gex

            if not call_gex_map or not put_gex_map:
                raise ValueError("Insufficient open interest in options chain.")

            call_wall_gld = max(call_gex_map, key=call_gex_map.get)
            put_wall_gld = min(put_gex_map, key=put_gex_map.get)

            strikes = sorted(list(set(call_gex_map.keys()) | set(put_gex_map.keys())))
            net_gex = [call_gex_map.get(k, 0.0) + put_gex_map.get(k, 0.0) for k in strikes]

            gamma_flip_gld = s_gld
            for i in range(len(strikes) - 1):
                if net_gex[i] * net_gex[i + 1] <= 0:
                    gamma_flip_gld = strikes[i]
                    break

            payload = {
                "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "status": "HEALTHY",
                "spot_xau": spot_xau,
                "gld_close": round(s_gld, 2),
                "conv_ratio": round(conv_ratio, 4),
                "call_wall_xau": round(call_wall_gld * conv_ratio, 2),
                "put_wall_xau": round(put_wall_gld * conv_ratio, 2),
                "gamma_flip_xau": round(gamma_flip_gld * conv_ratio, 2),
                "net_gamma_regime": "LONG_GAMMA_MEAN_REVERT" if sum(net_gex) > 0 else "SHORT_GAMMA_EXPANSION"
            }

            with open(CACHE_FILE, "w") as f:
                json.dump(payload, f, indent=2)

            return payload

        except Exception as err:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, "r") as f:
                    cache = json.load(f)
                cache["status"] = f"FALLBACK_DEGRADED: {str(err)}"
                return cache
            return {
                "status": "FAILED",
                "call_wall_xau": 99999.0,
                "put_wall_xau": 0.0,
                "gamma_flip_xau": spot_xau,
                "error": str(err)
            }

    @staticmethod
    def read_cached_levels() -> dict:
        """Lightweight reader for execution engines"""
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"status": "NO_CACHE", "call_wall_xau": 99999.0, "put_wall_xau": 0.0}
          
