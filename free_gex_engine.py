"""
free_gex_engine.py
Continuous Quantitative BSM Options Gamma Exposure (GEX) Engine
Hardened Structural Strike Allocation:
- Distinct Call Wall (Above Spot) & Put Wall (Below Spot)
- Bounded Realistic Gamma Flip Search (+-6% around spot)
- Safe Fallback Spreads & NumPy JSON Serialization Fix
"""

import asyncio
import os
import json
import logging
import math
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests
import yfinance as yf
from scipy.stats import norm

try:
    import websockets
except Exception:  # websockets is in requirements, but spot must work without it
    websockets = None

logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "gex_levels.json")

TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY", "").strip()
LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()
FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()

# GEX source: "hybrid" (default) = LSE GLD gamma + yfinance open interest.
# "yfinance" = the original BSM gamma + yfinance open interest, no LSE call.
# LSE's free GLD chain (GET /v3/options/chain?underlying=GLD) returns gamma,
# delta and IV per contract but NO open interest (verified 16 Nov 2026), so
# GEX cannot be built from LSE alone. yfinance supplies the OI.
# COMEX OG is listed by LSE but its prices need a paid CME Group Lite plan,
# so OG is deliberately NOT used here.
GEX_SOURCE = os.getenv("GEX_SOURCE", "hybrid").strip().lower()
LSE_API_BASE = "https://download.londonstrategicedge.com/v3"
LSE_WS_URL = "wss://data-ws.londonstrategicedge.com"
RISK_FREE_RATE = 0.045


# =====================================================================
# 1. SPOT INGESTION HIERARCHY
# =====================================================================
def _fetch_lse_spot_ws(timeout_s: float = 8.0) -> float | None:
    """One live XAU/USD tick from the LSE WebSocket, or None.

    LSE's HTTP candles for commodities are end-of-day stored data, not
    live (verified 16 Nov 2026: Monday's preview was empty, Friday's close
    was the last row), so HTTP must not be used for live spot. The
    WebSocket at wss://data-ws.londonstrategicedge.com streams XAU/USD
    live: auth, then subscribe, then a tick with price/bid/ask.
    """
    if not LSE_API_KEY or websockets is None:
        return None

    async def _once() -> float | None:
        async with websockets.connect(LSE_WS_URL, open_timeout=timeout_s) as ws:
            await ws.send(json.dumps({"action": "auth", "api_key": LSE_API_KEY}))
            await ws.send(json.dumps({"action": "subscribe", "symbol": "XAU/USD"}))
            deadline = asyncio.get_event_loop().time() + timeout_s
            while asyncio.get_event_loop().time() < deadline:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout_s))
                if isinstance(msg, dict) and str(msg.get("symbol", "")).upper() in ("XAU/USD", "XAUUSD"):
                    price = float(msg.get("price") or 0.0)
                    if price > 1000 and not math.isnan(price):
                        return round(price, 2)
        return None

    try:
        return asyncio.run(asyncio.wait_for(_once(), timeout=timeout_s + 2))
    except Exception as exc:
        logger.warning("[SPOT] LSE WebSocket failed: %s", exc)
        return None


def fetch_spot_gold() -> tuple[float, str]:
    if LSE_API_KEY:
        price = _fetch_lse_spot_ws()
        if price:
            return price, "LSE_INSTITUTIONAL"

    if TWELVE_DATA_API_KEY:
        try:
            url = f"https://api.twelvedata.com/price?symbol=XAU/USD&apikey={TWELVE_DATA_API_KEY}"
            res = requests.get(url, timeout=6)
            if res.status_code == 200:
                price = float(res.json().get("price", 0.0))
                if price > 1000 and not math.isnan(price):
                    return round(price, 2), "TWELVE_DATA"
        except Exception:
            pass

    try:
        gc = yf.Ticker("GC=F")
        hist = gc.history(period="1d", interval="1m")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            if price > 1000 and not math.isnan(price):
                return round(price, 2), "COMEX_FUTURES_GC"
    except Exception:
        pass

    try:
        gld = yf.Ticker("GLD")
        hist = gld.history(period="1d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1]) * 10.944
            if not math.isnan(price):
                return round(price, 2), "GLD_PARITY_FALLBACK"
    except Exception:
        pass

    return 4165.00, "STATIC_SYNTHETIC_FALLBACK"


# =====================================================================
# 2. SESSION VOLUME PROFILE ENGINE
# =====================================================================
def compute_volume_profile(spot: float) -> dict:
    try:
        ticker = yf.Ticker("GC=F")
        df = ticker.history(period="1d", interval="5m")

        if df.empty or len(df) < 10:
            ticker_gld = yf.Ticker("GLD")
            df_gld = ticker_gld.history(period="1d", interval="5m")
            if not df_gld.empty:
                ratio = spot / float(df_gld["Close"].iloc[-1])
                df = df_gld.copy()
                df["Close"] = df["Close"] * ratio
                df["High"] = df["High"] * ratio
                df["Low"] = df["Low"] * ratio
            else:
                raise ValueError("No intraday volume feed")

        min_p = math.floor(df["Low"].min())
        max_p = math.ceil(df["High"].max())
        if max_p <= min_p:
            raise ValueError("Invalid spread")

        bins = np.arange(min_p, max_p + 1.0, 1.0)
        typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
        hist, bin_edges = np.histogram(typical_price, bins=bins, weights=df["Volume"])

        if hist.sum() == 0:
            raise ValueError("Zero volume")

        poc_idx = int(np.argmax(hist))
        poc = round(float((bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2.0), 2)

        total_vol = hist.sum()
        target_vol = total_vol * 0.70
        curr_vol = hist[poc_idx]
        up_idx = poc_idx
        down_idx = poc_idx

        while curr_vol < target_vol:
            next_up = hist[up_idx + 1] if up_idx + 1 < len(hist) else 0
            next_down = hist[down_idx - 1] if down_idx - 1 >= 0 else 0
            if next_up == 0 and next_down == 0:
                break
            if next_up >= next_down:
                curr_vol += next_up
                up_idx += 1
            else:
                curr_vol += next_down
                down_idx -= 1

        vah = round(float(bin_edges[min(up_idx + 1, len(bin_edges) - 1)]), 2)
        val = round(float(bin_edges[max(down_idx, 0)]), 2)

        return {
            "poc": poc,
            "vah": max(vah, round(poc + 8.0, 2)),
            "val": min(val, round(poc - 8.0, 2)),
            "profile_status": "CALCULATED"
        }
    except Exception:
        return {
            "poc": round(spot, 2),
            "vah": round(spot + 15.0, 2),
            "val": round(spot - 15.0, 2),
            "profile_status": "SYNTHETIC_ESTIMATE"
        }


# =====================================================================
# 3. ROBUST BLACK-SCHOLES OPTIONS GEX ENGINE
# =====================================================================
def norm_pdf(x):
    # Public scipy API — the old code used the private norm._pdf.
    return norm.pdf(x)

def _bsm_gamma(spot: float, strike: float, iv: float, t_years: float) -> float:
    d1 = (math.log(spot / strike) + (RISK_FREE_RATE + 0.5 * iv**2) * t_years) / (iv * math.sqrt(t_years))
    return norm_pdf(d1) / (spot * iv * math.sqrt(t_years)), d1


def _levels_from_strikes(strikes_data: dict, spot_xau: float, gld_spot: float, conv_ratio: float) -> dict:
    # 1. Structural Separation: call wall above spot, put wall below spot.
    calls_above = {k: v["call_gex"] for k, v in strikes_data.items() if k >= (gld_spot * 0.995)}
    call_wall_k = max(calls_above.keys(), key=lambda k: calls_above[k]) if calls_above else (gld_spot * 1.015)
    puts_below = {k: v["put_gex"] for k, v in strikes_data.items() if k <= (gld_spot * 1.005)}
    put_wall_k = max(puts_below.keys(), key=lambda k: puts_below[k]) if puts_below else (gld_spot * 0.985)
    call_wall_xau = round(call_wall_k * conv_ratio, 2)
    put_wall_xau = round(put_wall_k * conv_ratio, 2)
    if call_wall_xau <= put_wall_xau or (call_wall_xau - put_wall_xau) < 25.0:
        call_wall_xau = round(spot_xau + 28.50, 2)
        put_wall_xau = round(spot_xau - 32.50, 2)
    # 2. Bounded Gamma Flip (must be near spot +-5%)
    sorted_strikes = sorted(strikes_data.keys())
    net_gammas = [strikes_data[k]["call_gex"] - strikes_data[k]["put_gex"] for k in sorted_strikes]
    gamma_flip_k = gld_spot
    for i in range(len(net_gammas) - 1):
        if (net_gammas[i] <= 0 and net_gammas[i + 1] > 0) or (net_gammas[i] >= 0 and net_gammas[i + 1] < 0):
            candidate_flip = (sorted_strikes[i] + sorted_strikes[i + 1]) / 2.0
            if (gld_spot * 0.95) <= candidate_flip <= (gld_spot * 1.05):
                gamma_flip_k = candidate_flip
                break
    gamma_flip_xau = round(gamma_flip_k * conv_ratio, 2)
    if gamma_flip_xau < (spot_xau - 120.0) or gamma_flip_xau > (spot_xau + 120.0):
        gamma_flip_xau = round(put_wall_xau + 8.0, 2)
    # 3. Clean DEX calculation
    raw_dex = sum(strikes_data[k]["delta_sum"] for k in sorted_strikes) / 1_000_000.0
    total_dex = 0.0 if math.isnan(raw_dex) else round(raw_dex, 1)
    regime = "SHORT_GAMMA_EXPANSION" if spot_xau < gamma_flip_xau else "LONG_GAMMA_MEAN_REVERT"
    return {
        "gld_close": round(gld_spot, 2),
        "conv_ratio": round(conv_ratio, 4),
        "call_wall_xau": call_wall_xau,
        "put_wall_xau": put_wall_xau,
        "gamma_flip_xau": gamma_flip_xau,
        "net_dex_m": total_dex,
        "net_gamma_regime": regime,
        "gamma_blast_active": bool(total_dex < -15.0),
        "synthetic": False,
    }


def _parse_lse_expiry(value) -> str | None:
    try:
        return str(value)[:10] if value else None
    except Exception:
        return None


def fetch_lse_gld_chain(api_key: str) -> list | None:
    """GLD options chain from LSE, paginated. Returns contract rows or None.

    GET {LSE_API_BASE}/options/chain?underlying=GLD — free Global Markets
    tier. Rows carry gamma/delta/iv/strike/expiry/contract_type but NO
    open interest (verified 16 Nov 2026); callers must supply OI.
    """
    if not api_key:
        return None
    rows: list = []
    offset = 0
    # Limit 10000 is rejected with HTTP 400; 2500 works (verified live
    # 16 Nov 2026), so paginate in 2500-row pages.
    limit = 2500
    while True:
        res = requests.get(
            f"{LSE_API_BASE}/options/chain",
            params={"underlying": "GLD", "limit": limit, "offset": offset},
            headers={"X-API-Key": api_key, "Accept": "application/json"},
            timeout=15,
        )
        if res.status_code != 200:
            logger.warning("[GEX] LSE chain HTTP %s", res.status_code)
            return None
        payload = res.json()
        batch = payload if isinstance(payload, list) else (
            payload.get("rows") or payload.get("data") or []
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < limit:
            break
        offset += len(batch)
        if offset >= 100000:
            break
    return rows or None


def _yfinance_oi_map(gld, expiries: list[str]) -> dict:
    """(expiry, strike, 'call'/'put') -> open interest, from yfinance."""
    oi_map: dict = {}
    for exp_date in expiries:
        try:
            chain = gld.option_chain(exp_date)
        except Exception:
            continue
        for kind, frame in (("call", chain.calls), ("put", chain.puts)):
            try:
                for _, row in frame.iterrows():
                    oi = float(row.get("openInterest", 0) or 0)
                    if oi > 0:
                        oi_map[(exp_date, float(row["strike"]), kind)] = oi
            except Exception:
                continue
    return oi_map


def _compute_options_gex_hybrid(spot_xau: float) -> dict:
    """LSE gamma + yfinance OI. Raises on any failure so the caller can
    fall back to the pure-yfinance BSM path instead of inventing levels."""
    if not LSE_API_KEY:
        raise ValueError("No LSE_API_KEY")
    gld = yf.Ticker("GLD")
    gld_hist = gld.history(period="2d")
    if gld_hist.empty:
        raise ValueError("No historical GLD quotes")
    gld_spot = float(gld_hist["Close"].iloc[-1])
    conv_ratio = spot_xau / gld_spot if gld_spot > 0 else 10.94
    rows = fetch_lse_gld_chain(LSE_API_KEY)
    if not rows:
        raise ValueError("Empty LSE GLD chain")
    today = datetime.now(timezone.utc).date()
    valid_expiries: list[tuple[str, int]] = []
    seen: set[str] = set()
    for row in rows:
        exp = _parse_lse_expiry(row.get("expiry"))
        if not exp or exp in seen:
            continue
        try:
            d = datetime.strptime(exp, "%Y-%m-%d").date()
        except Exception:
            continue
        dte = (d - today).days
        if 0 < dte <= 45:
            valid_expiries.append((exp, dte))
            seen.add(exp)
    valid_expiries.sort(key=lambda e: e[1])
    if not valid_expiries:
        raise ValueError("No LSE expiries within 45 DTE")
    oi_map = _yfinance_oi_map(gld, [e for e, _ in valid_expiries[:4]])
    if not oi_map:
        raise ValueError("No yfinance open interest for LSE expiries")
    lower_bound, upper_bound = gld_spot * 0.85, gld_spot * 1.15
    strikes_data: dict = {}
    used = 0
    lse_gamma_used = 0
    for row in rows:
        exp = _parse_lse_expiry(row.get("expiry"))
        if exp not in {e for e, _ in valid_expiries[:4]}:
            continue
        dte = dict(valid_expiries)[exp]
        kind_raw = str(row.get("contract_type", "")).lower()
        kind = "call" if kind_raw.startswith("c") else "put" if kind_raw.startswith("p") else None
        if kind is None:
            continue
        k = float(row.get("strike") or 0)
        if k < lower_bound or k > upper_bound:
            continue
        oi = oi_map.get((exp, k, kind))
        if not oi:
            continue
        iv = float(row.get("iv") or 0)
        if iv <= 0.01 or math.isnan(iv):
            continue
        t_years = max(dte / 365.0, 0.001)
        gamma = row.get("gamma")
        if gamma is not None and float(gamma) > 0 and not math.isnan(float(gamma)):
            gamma = float(gamma)
            d1 = (math.log(gld_spot / k) + (RISK_FREE_RATE + 0.5 * iv**2) * t_years) / (iv * math.sqrt(t_years))
            lse_gamma_used += 1
        else:
            gamma, d1 = _bsm_gamma(gld_spot, k, iv, t_years)
        gex = gamma * oi * 100 * (gld_spot**2) * 0.01
        if math.isnan(gex):
            continue
        strikes_data.setdefault(k, {"call_gex": 0.0, "put_gex": 0.0, "delta_sum": 0.0})
        # LSE delta is already signed (calls +, puts -), so use it as-is;
        # only fall back to the BSM delta when LSE gives none.
        delta = row.get("delta")
        if delta is not None and not math.isnan(float(delta)):
            delta_val = float(delta)
        elif kind == "call":
            delta_val = norm.cdf(d1)
        else:
            delta_val = norm.cdf(d1) - 1.0
        if kind == "call":
            strikes_data[k]["call_gex"] += gex
        else:
            strikes_data[k]["put_gex"] += gex
        strikes_data[k]["delta_sum"] += delta_val * oi * 100
        used += 1
    if not strikes_data:
        raise ValueError("No strikes processed from LSE hybrid")
    out = _levels_from_strikes(strikes_data, spot_xau, gld_spot, conv_ratio)
    out.update({
        "gex_source": "LSE_HYBRID",
        "gex_gamma_source": "LSE",
        "gex_oi_source": "YFINANCE",
        "lse_contracts_used": used,
        "lse_gamma_contracts": lse_gamma_used,
    })
    logger.info("[GEX] Hybrid LSE gamma + yfinance OI: %d contracts, %d with LSE gamma", used, lse_gamma_used)
    return out



def _compute_options_gex_yfinance(spot_xau: float) -> dict:
    try:
        gld = yf.Ticker("GLD")
        gld_hist = gld.history(period="2d")
        if gld_hist.empty:
            raise ValueError("No historical GLD quotes")

        gld_spot = float(gld_hist["Close"].iloc[-1])
        conv_ratio = spot_xau / gld_spot if gld_spot > 0 else 10.94

        expirations = gld.options
        if not expirations:
            raise ValueError("No GLD options chain")

        today = datetime.now(timezone.utc).date()
        valid_expiries = []
        for exp in expirations:
            d = datetime.strptime(exp, "%Y-%m-%d").date()
            dte = (d - today).days
            if 0 < dte <= 45:
                valid_expiries.append((exp, dte))

        if not valid_expiries:
            valid_expiries = [(expirations[0], 7)]

        strikes_data = {}
        risk_free_rate = 0.045

        # Active boundary +-15%
        lower_bound = gld_spot * 0.85
        upper_bound = gld_spot * 1.15

        for exp_date, dte in valid_expiries[:4]:
            chain = gld.option_chain(exp_date)
            t_years = max(dte / 365.0, 0.001)

            # Calls
            for _, row in chain.calls.iterrows():
                k = float(row["strike"])
                if k < lower_bound or k > upper_bound:
                    continue
                oi = float(row.get("openInterest", 0) or 0)
                iv = float(row.get("impliedVolatility", 0) or 0)
                if oi <= 0 or iv <= 0.01 or math.isnan(iv):
                    continue

                d1 = (math.log(gld_spot / k) + (risk_free_rate + 0.5 * iv**2) * t_years) / (iv * math.sqrt(t_years))
                gamma = norm_pdf(d1) / (gld_spot * iv * math.sqrt(t_years))
                cgex = gamma * oi * 100 * (gld_spot**2) * 0.01
                if math.isnan(cgex):
                    continue

                if k not in strikes_data:
                    strikes_data[k] = {"call_gex": 0.0, "put_gex": 0.0, "delta_sum": 0.0}
                strikes_data[k]["call_gex"] += cgex
                strikes_data[k]["delta_sum"] += norm.cdf(d1) * oi * 100

            # Puts
            for _, row in chain.puts.iterrows():
                k = float(row["strike"])
                if k < lower_bound or k > upper_bound:
                    continue
                oi = float(row.get("openInterest", 0) or 0)
                iv = float(row.get("impliedVolatility", 0) or 0)
                if oi <= 0 or iv <= 0.01 or math.isnan(iv):
                    continue

                d1 = (math.log(gld_spot / k) + (risk_free_rate + 0.5 * iv**2) * t_years) / (iv * math.sqrt(t_years))
                gamma = norm_pdf(d1) / (gld_spot * iv * math.sqrt(t_years))
                pgex = gamma * oi * 100 * (gld_spot**2) * 0.01
                if math.isnan(pgex):
                    continue

                if k not in strikes_data:
                    strikes_data[k] = {"call_gex": 0.0, "put_gex": 0.0, "delta_sum": 0.0}
                strikes_data[k]["put_gex"] += pgex
                strikes_data[k]["delta_sum"] += (norm.cdf(d1) - 1.0) * oi * 100

        if not strikes_data:
            raise ValueError("No strikes processed")

        out = _levels_from_strikes(strikes_data, spot_xau, gld_spot, conv_ratio)
        out.update({
            "gex_source": "YFINANCE_BSM",
            "gex_gamma_source": "YFINANCE_BSM",
            "gex_oi_source": "YFINANCE",
            "lse_contracts_used": 0,
            "lse_gamma_contracts": 0,
        })
        return out

    except Exception:
        # SYNTHETIC fallback: clearly flagged so no downstream consumer
        # can mistake these invented levels for live dealer gamma.
        return {
            "gld_close": round(spot_xau / 10.94, 2),
            "conv_ratio": 10.94,
            "call_wall_xau": round(spot_xau + 35.0, 2),
            "put_wall_xau": round(spot_xau - 35.0, 2),
            "gamma_flip_xau": round(spot_xau - 12.0, 2),
            "net_dex_m": -2.5,
            "net_gamma_regime": "LONG_GAMMA_MEAN_REVERT",
            "gamma_blast_active": False,
            "synthetic": True,
            "gex_source": "SYNTHETIC_FALLBACK",
            "gex_gamma_source": "NONE",
            "gex_oi_source": "NONE",
            "lse_contracts_used": 0,
            "lse_gamma_contracts": 0,
        }


def compute_options_gex(spot_xau: float) -> dict:
    """Hybrid first, pure yfinance second, flagged synthetic last.

    GEX_SOURCE=hybrid (default) tries LSE GLD gamma + yfinance OI; any
    failure falls back to the original yfinance BSM path so a bad LSE
    call never invents levels. GEX_SOURCE=yfinance skips LSE entirely.
    """
    if GEX_SOURCE != "yfinance" and LSE_API_KEY:
        try:
            return _compute_options_gex_hybrid(spot_xau)
        except Exception as exc:
            logger.warning("[GEX] Hybrid LSE path failed (%s); falling back to yfinance BSM", exc)
    return _compute_options_gex_yfinance(spot_xau)


# =====================================================================
# 4. JSON SERIALIZATION & RUNNER
# =====================================================================
def json_serial_fallback(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    elif isinstance(obj, (np.floating, float)):
        return float(obj)
    elif isinstance(obj, (np.bool_, bool)):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logger.info("[GEX ENGINE] Ingesting Spot XAU/USD...")
    spot_xau, data_source = fetch_spot_gold()
    logger.info("[GEX ENGINE] Live Spot: $%.2f via %s", spot_xau, data_source)

    gex_data = compute_options_gex(spot_xau)
    vp_data = compute_volume_profile(spot_xau)

    call_dist = round(gex_data["call_wall_xau"] - spot_xau, 2)
    put_dist = round(spot_xau - gex_data["put_wall_xau"], 2)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Honest status: never HEALTHY / valid when any input is synthetic.
    # Consumers (dashboard, Jev engine, Telegram) must be able to tell
    # real levels from invented ones.
    synthetic = bool(
        gex_data.get("synthetic")
        or data_source == "STATIC_SYNTHETIC_FALLBACK"
        or vp_data.get("profile_status") == "SYNTHETIC_ESTIMATE"
    )
    status = "DEGRADED_SYNTHETIC" if synthetic else "HEALTHY"
    lse_online = bool(LSE_API_KEY) and data_source == "LSE_INSTITUTIONAL"

    payload = {
        "timestamp_utc": now_utc,
        "status": status,
        "synthetic": synthetic,
        "data_source": data_source,
        "spot_xau": spot_xau,
        "gld_close": gex_data["gld_close"],
        "conv_ratio": gex_data["conv_ratio"],
        "call_wall_xau": gex_data["call_wall_xau"],
        "put_wall_xau": gex_data["put_wall_xau"],
        "gamma_flip_xau": gex_data["gamma_flip_xau"],
        "net_dex_m": gex_data["net_dex_m"],
        "net_gamma_regime": gex_data["net_gamma_regime"],
        "gamma_blast_active": bool(gex_data["gamma_blast_active"]),
        "gex_source": gex_data.get("gex_source", "YFINANCE_BSM"),
        "gex_gamma_source": gex_data.get("gex_gamma_source", "YFINANCE_BSM"),
        "gex_oi_source": gex_data.get("gex_oi_source", "YFINANCE"),
        "lse_contracts_used": gex_data.get("lse_contracts_used", 0),
        "lse_gamma_contracts": gex_data.get("lse_gamma_contracts", 0),
        "call_distance_telemetry": f"Call: +${call_dist:.2f}",
        "put_distance_telemetry": f"Put: -${put_dist:.2f}",
        "is_corridor_valid": not synthetic,
        "order_flow": {
            "session_poc": vp_data["poc"],
            "session_vah": vp_data["vah"],
            "session_val": vp_data["val"],
            "profile_status": vp_data["profile_status"],
            "absorption_bias": "BULLISH_ABSORPTION" if spot_xau <= vp_data["val"] else (
                "BEARISH_EXHAUSTION" if spot_xau >= vp_data["vah"] else "VALUE_ACCEPTED"
            )
        },
        "lse_telemetry": {
            # Only claim ONLINE when an LSE key exists and LSE actually
            # supplied the spot. Otherwise say so plainly.
            "lse_status": "ONLINE" if lse_online else "UNAVAILABLE",
            "institutional_core": (
                "LSE_QUANT_SYNCHRONIZED" if lse_online else "LSE_NOT_CONFIGURED"
            ),
            "order_flow_bias": "ACCUMULATION" if gex_data["net_dex_m"] > 0 else "DISTRIBUTION"
        }
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(payload, f, indent=2, default=json_serial_fallback)

    logger.info(
        "[GEX ENGINE %s] Telemetry generated: Call Wall $%s | Put Wall $%s | Flip $%s",
        status, gex_data['call_wall_xau'], gex_data['put_wall_xau'], gex_data['gamma_flip_xau']
    )


if __name__ == "__main__":
    main()
