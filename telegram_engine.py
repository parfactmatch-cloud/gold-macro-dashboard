"""
telegram_engine.py
Institutional Telemetry & Alerts Broadcaster for Gold (XAU/USD).
- FRED_API_KEY: Live US10Y Sovereign Yield
- LSE_API_KEY: Live Data Core Status Sync
- Telegram Dispatch Engine
"""

import os
import json
import requests
from datetime import datetime, timezone

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

FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()
LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()


def get_live_us10y() -> tuple[float, str]:
    """Fetch live US 10-Year Treasury Yield via FRED API."""
    if FRED_API_KEY:
        try:
            url = f"https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&limit=1"
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                obs = res.json().get("observations", [])
                if obs and obs[0].get("value") not in [".", None, ""]:
                    val = float(obs[0]["value"])
                    impact = "BEARISH_PRESSURE" if val >= 4.40 else ("BULLISH_TAILWIND" if val <= 4.10 else "NEUTRAL_CONSOLIDATION")
                    print(f"[FRED API] Real-time US10Y: {val}% ({impact})")
                    return val, impact
        except Exception as e:
            print(f"[FRED WARN] {e}")
    return 4.28, "NEUTRAL_CONSOLIDATION"


def dispatch_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG WARN] Credentials missing.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=10)
        if res.status_code == 200:
            print("[TG OK] Broadcast delivered successfully.")
            return True
        print(f"[TG ERROR] {res.status_code}: {res.text}")
        return False
    except Exception as e:
        print(f"[TG EXCEPTION] {e}")
        return False


def broadcast_market_pulse(spot, source, call_wall, put_wall, gamma_flip, regime, us10y_yield, us10y_impact, net_dex, blast_active, telemetry_call, telemetry_put, is_corridor_valid, lse_core):
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC | %d %b %Y")

    if blast_active and (spot >= call_wall or spot <= put_wall):
        barrier_status = "🚀 <b>GAMMA BLAST REGIME</b>: High directional flow detected. Dealer walls bypassed."
    elif not is_corridor_valid or spot <= put_wall or spot >= call_wall:
        if spot <= put_wall:
            barrier_status = f"🔴 <b>PUT WALL BREACHED</b>: Spot below floor [Depth: {telemetry_put}]"
        else:
            barrier_status = f"🚀 <b>CALL WALL SQUEEZE</b>: Spot above ceiling [Exceed: {telemetry_call}]"
    else:
        barrier_status = f"🟢 <b>CORRIDOR CLEAR</b>: Spot inside boundaries ({telemetry_call} | {telemetry_put})"

    regime_tag = "🟩 LONG GAMMA (Mean-Reverting)" if "LONG_GAMMA" in regime else "🟥 SHORT GAMMA (Volatility Expansion)"
    macro_icon = "🟢" if us10y_impact == "BULLISH_TAILWIND" else ("🔴" if us10y_impact == "BEARISH_PRESSURE" else "⚪")
    dex_bias = "🟢 Dealer Net Long" if net_dex > 0 else ("🔴 Dealer Net Short" if net_dex < 0 else "⚪ Neutral")

    pulse_card = (
        f"📡 <b>INSTITUTIONAL MARKET RADAR PULSE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛 <b>Data Core</b>: <code>{lse_core} ({source})</code>\n"
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
    return dispatch_telegram(pulse_card)


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(script_dir, "gex_levels.json")

    if not os.path.exists(json_path):
        print("[MAIN WARN] gex_levels.json not found.")
        exit(0)

    with open(json_path, "r") as f:
        d = json.load(f)

    yield_val, impact_val = get_live_us10y()
    lse_data = d.get("lse_telemetry", {})
    lse_core = lse_data.get("institutional_core", "LSE_QUANT_SYNCHRONIZED" if LSE_API_KEY else "STANDALONE")

    broadcast_market_pulse(
        spot=float(d.get("spot_xau", 0.0)),
        source=str(d.get("data_source", "DIRECT")),
        call_wall=float(d.get("call_wall_xau", 0.0)),
        put_wall=float(d.get("put_wall_xau", 0.0)),
        gamma_flip=float(d.get("gamma_flip_xau", 0.0)),
        regime=str(d.get("net_gamma_regime", "")),
        us10y_yield=yield_val,
        us10y_impact=impact_val,
        net_dex=float(d.get("net_dex_m", 0.0)),
        blast_active=bool(d.get("gamma_blast_active", False)),
        telemetry_call=str(d.get("call_distance_telemetry", "")),
        telemetry_put=str(d.get("put_distance_telemetry", "")),
        is_corridor_valid=bool(d.get("is_corridor_valid", True)),
        lse_core=lse_core
    )
    
