"""
free_gex_engine.py
Institutional SPDR Gold Shares (GLD) Options Gamma & Delta Engine.
Computes Black-Scholes Dealer GEX, Net Delta Exposure (DEX), and Gamma Blast Exemption.
Projects critical institutional walls and squeeze states to Spot XAU/USD.
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
    def _bsm_greeks(S: float, K: float, T: float, r: float, sigma: float) -> tuple:
        """
        Returns (gamma, delta_call, delta_put) using analytical Black-Scholes.
        """
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.0, 0.0, 0.0
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        gamma = float(norm.pdf(d1) / (S * sigma * np.sqrt(T)))
        delta_call = float(norm.cdf(d1))
        delta_put = float(delta_call - 1.0)
        return gamma, delta_call, delta_put

    def compute_gex(self, spot_xau: float) -> dict:
        """
        Fetches front options chains for GLD, computes GEX & DEX,
        evaluates Gamma Blast / Squeeze eligibility, and caches levels.
        """
        try:
            gld = yf.Ticker("GLD")
            hist = gld.history(period="5d")
            if hist.empty:
                raise ValueError("GLD quote fetch failed.")
            
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

            for exp_str, T in valid_expiries:
                chain = gld.option_chain(exp_str)
                
                # Calls: Dealer Long Gamma (+), Dealer Short Stock to hedge customer long calls (-)
                for _, row in chain.calls.iterrows():
                    strike = float(row["strike"])
                    oi = float(row["openInterest"]) if not np.isnan(row["openInterest"]) else 0.0
                    iv = float(row["impliedVolatility"]) if not np.isnan(row["impliedVolatility"]) else 0.0
                    if oi > 0 and iv > 0.01:
                        gamma, d_call, _ = self._bsm_greeks(s_gld, strike, T, self.r, iv)
                        # GEX in Dollar Notional Exposure ($)
                        gex = gamma * oi * 100.0 * (s_gld ** 2) * 0.01
                        call_gex_map[strike] = call_gex_map.get(strike, 0.0) + gex
                        # Net Delta Exposure: Shares equivalent / 1M scaling
                        total_call_dex += (d_call * oi * 100.0 * s_gld) / 1_000_000.0

                # Puts: Dealer Short Gamma (-), Dealer Long Stock to hedge customer long puts (+)
                for _, row in chain.puts.iterrows():
                    strike = float(row["strike"])
                    oi = float(row["openInterest"]) if not np.isnan(row["openInterest"]) else 0.0
                    iv = float(row["impliedVolatility"]) if not np.isnan(row["impliedVolatility"]) else 0.0
                    if oi > 0 and iv > 0.01:
                        gamma, _, d_put = self._bsm_greeks(s_gld, strike, T, self.r, iv)
                        gex = gamma * oi * 100.0 * (s_gld ** 2) * 0.01
                        put_gex_map[strike] = put_gex_map.get(strike, 0.0) - gex
                        total_put_dex += (abs(d_put) * oi * 100.0 * s_gld) / 1_000_000.0

            if not call_gex_map or not put_gex_map:
                raise ValueError("Insufficient open interest in options chain.")

            call_wall_gld = max(call_gex_map, key=call_gex_map.get)
            put_wall_gld = min(put_gex_map, key=put_gex_map.get)

            strikes = sorted(list(set(call_gex_map.keys()) | set(put_gex_map.keys())))
            net_gex = [call_gex_map.get(k, 0.0) + put_gex_map.get(k, 0.0) for k in strikes]
            total_net_gex = sum(net_gex)

            gamma_flip_gld = s_gld
            for i in range(len(strikes) - 1):
                if net_gex[i] * net_gex[i + 1] <= 0:
                    gamma_flip_gld = strikes[i]
                    break

            # Dealer Net Delta: Positive = Dealer net long delta (supports bids), Negative = Short
            net_dex_m = round(total_call_dex - total_put_dex, 2)

            # Gamma Blast / Squeeze Exemption Condition:
            # Dealer in SHORT GAMMA regime (must chase direction) + high directional delta pressure
            gamma_blast_active = (total_net_gex < 0) and (abs(net_dex_m) >= 8.0)

            payload = {
                "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
                "status": "HEALTHY",
                "spot_xau": spot_xau,
                "gld_close": round(s_gld, 2),
                "conv_ratio": round(conv_ratio, 4),
                "call_wall_xau": round(call_wall_gld * conv_ratio, 2),
                "put_wall_xau": round(put_wall_gld * conv_ratio, 2),
                "gamma_flip_xau": round(gamma_flip_gld * conv_ratio, 2),
                "net_dex_m": net_dex_m,
                "net_gamma_regime": "LONG_GAMMA_MEAN_REVERT" if total_net_gex > 0 else "SHORT_GAMMA_EXPANSION",
                "gamma_blast_active": bool(gamma_blast_active)
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
                "net_dex_m": 0.0,
                "gamma_blast_active": False,
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
        return {
            "status": "NO_CACHE",
            "call_wall_xau": 99999.0,
            "put_wall_xau": 0.0,
            "net_dex_m": 0.0,
            "gamma_blast_active": False
                }
                    
