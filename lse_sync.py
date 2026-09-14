"""
LSE Isolated Macro & Options GEX/DEX Sync Engine
- Fetches US10Y Bond Yields from London Strategic Edge API -> saves to 'lse_macro.csv'
- Computes SPDR Gold Shares (GLD) dealer Gamma Walls, Net DEX & Blast Squeeze -> saves to 'gex_levels.json'
- Multi-Source Spot Feed: CME Futures (GC=F) -> Stooq XAUUSD -> Binance PAXGUSDT -> GLD NAV
- Regime-agnostic: Directly supports CME GC1! $4,400+ pricing without static baseline truncations
- Periodic Radar Telemetry: Bulletproof HTML Telegram dispatch with clean mathematical distance telemetry
"""

import os
import requests
import pandas as pd
from datetime import datetime, timezone

# GEX & DEX Engine Import (free_gex_engine.py must reside in the same execution path)
from free_gex_engine import GoldGEXEngine

# Centralized Telegram Engine Broadcast Import with Fallback Handling
try:
    from telegram_engine import broadcast_market_pulse
except ImportError:
    broadcast_market_pulse = None

LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN", "") 
    or os.getenv("BOT_TOKEN", "") 
    or os.getenv("TG_BOT_TOKEN", "")
).strip()
TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID", "") 
    or os.getenv("CHAT_ID", "") 
    or os.getenv("TG_CHAT_ID", "")
).strip()
LSE_MACRO_FILE = "lse_macro.csv"

def send_direct_telegram_pulse(
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
):
    """Direct, robust HTML Telegram dispatcher that prevents double-negative glitches & entity parse errors."""
    print(f"[TG AUTH CHECK] Token configured: {bool(TELEGRAM_BOT_TOKEN)} | Chat ID configured: {bool(TELEGRAM_CHAT_ID)}")
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG CRITICAL] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing from environment variables!")
        return

    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC | %d %b %Y")

    # Corridor and Barrier Status Calculation using Patched Absolute Boundaries
    if blast_active:
        barrier_status = "🚀 <b>GAMMA BLAST REGIME</b>: High directional flow detected. Dealer walls in squeeze bypass mode."
    elif not is_corridor_valid or spot < put_wall or spot > call_wall:
        if spot <= put_wall:
            barrier_status = f"🔴 <b>PUT WALL BREACHED</b>: Spot trading below floor (${put_wall:.2f}) [Depth: {telemetry_put}]"
        else:
            barrier_status = f"🚀 <b>CALL WALL SQUEEZE</b>: Spot trading above ceiling (${call_wall:.2f}) [Exceed: {telemetry_call}]"
    else:
        barrier_status = f"🟢 <b>CORRIDOR CLEAR</b>: Spot inside boundaries ({telemetry_call} | {telemetry_put})"

    regime_tag = "🟩 LONG GAMMA (Mean-Reverting)" if "LONG_GAMMA" in regime else "🟥 SHORT GAMMA (Volatility Expansion)"
    macro_icon = "🟢" if us10y_impact == "BULLISH_TAILWIND" else ("🔴" if us10y_impact == "BEARISH_PRESSURE" else "⚪")
    dex_bias = "Dealer Net Long" if net_dex > 0 else ("Dealer Net Short" if net_dex < 0 else "Neutral")

    html_card = (
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

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    # Primary attempt: Safe HTML Parse Mode
    try:
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": html_card, "parse_mode": "HTML"}, timeout=10)
        if res.status_code == 200:
            print("[PULSE SUCCESS] Institutional Market Radar Pulse delivered to Telegram (HTML).")
            return
        else:
            print(f"[PULSE HTML FAILED] HTTP {res.status_code}: {res.text}. Trying fallback plain text...")
    except Exception as e:
        print(f"[PULSE NETWORK EXCEPTION] {e}")

    # Fallback attempt: Clean Plain Text
    try:
        clean_status = barrier_status.replace('<b>', '').replace('</b>', '')
        raw_text = (
            f"📡 INSTITUTIONAL MARKET RADAR PULSE\n"
            f"Spot: ${spot:.2f} | US10Y: {us10y_yield:.2f}% ({us10y_impact})\n"
            f"Call Wall: ${call_wall:.2f} | Put Wall: ${put_wall:.2f} | Flip: ${gamma_flip:.2f}\n"
            f"Net DEX: {net_dex:+.1f}M | Blast Active: {blast_active}\n"
            f"Status: {clean_status}\n"
            f"Synced: {now_utc}"
        )
        res2 = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": raw_text}, timeout=10)
        if res2.status_code == 200:
            print("[PULSE SUCCESS] Institutional Market Radar delivered via plain text fallback.")
        else:
            print(f"[PULSE HARD FAILURE] Telegram API rejected: {res2.status_code} - {res2.text}")
    except Exception as e:
        print(f"[PULSE CRITICAL ERROR] {e}")

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
                return pd.DataFrame(data)
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
    # 1. Primary: CME Continuous Gold Futures (GC=F) with Session & 7-day Buffer
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
            f"Net DEX: {gex_data.get('net_dex_m'):+.1f}M | "
            f"Telemetry: {gex_data.get('call_distance_telemetry')} / {gex_data.get('put_distance_telemetry')} | "
            f"Status: {gex_data.get('status')}"
        )
        return gex_data
    except Exception as e:
        print(f"[GEX SYNC ERROR] Calculation aborted: {e}")
        return None

def run_sync():
    print(f"[LSE SYNC RUN] UTC: {datetime.now(timezone.utc).strftime('%H:%M:%S')}")
    
    # 1. Fetch US10Y Bond Yield (Macro Directional Filter)
    latest_val = 0.0
    gold_macro_impact = "NEUTRAL"
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

    # 2. Options Gamma & Delta Exposure (GEX/DEX) Calculation
    spot_xau = fetch_live_gold_spot()
    gex_data = sync_gex(spot_price=spot_xau)

    # 3. Guaranteed Periodic Market Radar Telemetry Dispatch
    print("[PULSE DISPATCH] Initiating Telegram Pulse Trigger...")
    if gex_data and gex_data.get("status") in ["HEALTHY", "FALLBACK_DEGRADED"]:
        call_wall = float(gex_data.get("call_wall_xau", 0.0))
        put_wall = float(gex_data.get("put_wall_xau", 0.0))
        gamma_flip = float(gex_data.get("gamma_flip_xau", 0.0))
        regime = str(gex_data.get("net_gamma_regime", "UNKNOWN"))
        net_dex = float(gex_data.get("net_dex_m", 0.0))
        blast_active = bool(gex_data.get("gamma_blast_active", False))
        
        # Pull clean pre-computed telemetry strings from patch
        telemetry_call = str(gex_data.get("call_distance_telemetry", f"Call: +${abs(call_wall - spot_xau):.2f}"))
        telemetry_put = str(gex_data.get("put_distance_telemetry", f"Put: +${abs(spot_xau - put_wall):.2f}"))
        is_corridor_valid = bool(gex_data.get("is_corridor_valid", put_wall < spot_xau < call_wall))

        # Try centralized broadcaster first, fallback to direct local method
        sent = False
        if broadcast_market_pulse is not None:
            try:
                sent = broadcast_market_pulse(
                    spot=spot_xau,
                    call_wall=call_wall,
                    put_wall=put_wall,
                    gamma_flip=gamma_flip,
                    regime=regime,
                    us10y_yield=latest_val,
                    us10y_impact=gold_macro_impact,
                    net_dex=net_dex,
                    blast_active=blast_active,
                    telemetry_call=telemetry_call,
                    telemetry_put=telemetry_put,
                    is_corridor_valid=is_corridor_valid
                )
            except Exception as e:
                print(f"[CENTRALIZED PULSE ERROR] {e}. Falling back to direct sender.")
                sent = False

        if not sent:
            send_direct_telegram_pulse(
                spot=spot_xau,
                call_wall=call_wall,
                put_wall=put_wall,
                gamma_flip=gamma_flip,
                regime=regime,
                us10y_yield=latest_val,
                us10y_impact=gold_macro_impact,
                net_dex=net_dex,
                blast_active=blast_active,
                telemetry_call=telemetry_call,
                telemetry_put=telemetry_put,
                is_corridor_valid=is_corridor_valid
            )
    else:
        print("[PULSE SKIPPED] GEX calculation payload degraded or missing.")

if __name__ == "__main__":
    run_sync()
    
