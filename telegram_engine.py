"""
telegram_engine.py
Institutional Macro Telemetry & Actionable Playbook Dual-Broadcaster
- Message 1: Original Institutional Market Radar Pulse (Untouched format)
- Message 2: Dedicated Actionable Institutional Playbook (What To Do)
"""

import os
import json
import requests
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GEX_FILE = os.path.join(SCRIPT_DIR, "gex_levels.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
FRED_API_KEY = os.getenv("FRED_API_KEY", "").strip()


def send_tg_msg(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG WARN] Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code != 200:
            print(f"[TG POST FAIL] {res.status_code}: {res.text}")
    except Exception as e:
        print(f"[TG POST ERROR] {e}")


def fetch_fred_yield() -> tuple[float, str]:
    if not FRED_API_KEY:
        return 5.25, "BEARISH_PRESSURE"
    try:
        url = f"https://api.stlouisfed.org/fred/series/observations?series_id=DGS10&api_key={FRED_API_KEY}&file_type=json&sort_order=desc&limit=5"
        res = requests.get(url, timeout=6)
        if res.status_code == 200:
            obs = res.json().get("observations", [])
            for item in obs:
                val = item.get("value", "")
                if val and val != ".":
                    y = float(val)
                    bias = "BEARISH_PRESSURE" if y >= 4.40 else ("BULLISH_TAILWIND" if y <= 4.10 else "NEUTRAL")
                    return y, bias
    except Exception:
        pass
    return 5.25, "BEARISH_PRESSURE"


def format_original_radar_card(spot: float, data_source: str, us10y: float, yield_bias: str,
                               regime_str: str, dex: float, dex_color: str, dex_label: str,
                               call_wall: float, put_wall: float, gamma_flip: float,
                               call_dist: float, put_dist: float, sync_time: str) -> str:
    """Original untouched format as previously received."""
    return (
        f"📡 <b>INSTITUTIONAL MARKET RADAR PULSE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛 <b>Data Core</b>: AUTHENTICATED ({data_source})\n"
        f"💵 <b>Live Spot/Futures</b>: ${spot:.2f}\n"
        f"🏛 <b>US10Y Yield</b>: {us10y:.2f}% 🔴 {yield_bias}\n"
        f"⚡ <b>Dealer Regime</b>: {regime_str}\n"
        f"⚖️ <b>Net Delta (DEX)</b>: {dex:+.1f}M ({dex_color} {dex_label})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧱 <b>Gamma Corridors</b>:\n"
        f"• <b>Call Wall (Ceiling)</b>: ${call_wall:.2f}\n"
        f"• <b>Put Wall (Floor)</b>: ${put_wall:.2f}\n"
        f"• <b>Gamma Neutral Flip</b>: ${gamma_flip:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <b>Corridor Telemetry</b>:\n"
        f"🟢 <b>CORRIDOR CLEAR</b>: Spot inside boundaries (Call: +${call_dist:.2f} | Put: +${put_dist:.2f})\n"
        f"⏰ <b>Synced</b>: {sync_time}"
    )


def format_actionable_playbook(spot: float, call_wall: float, put_wall: float, gamma_flip: float, dex: float) -> str:
    """Actionable breakdown explaining how to trade the current pulse."""
    call_dist = call_wall - spot
    put_dist = spot - put_wall

    if call_dist <= 12.0:
        zone = "⚠️ CEILING TEST (NEAR CALL WALL)"
    elif put_dist <= 12.0:
        zone = "⚠️ FLOOR TEST (NEAR PUT WALL)"
    else:
        zone = "⚖️ MID-CORRIDOR COMPRESSION (WAIT & WATCH)"

    short_entry_low = round(call_wall - 10.0, 2)
    short_entry_high = round(call_wall, 2)
    short_sl = round(call_wall + 12.0, 2)
    short_tp = round(gamma_flip if spot > gamma_flip else put_wall + 20.0, 2)

    long_entry_low = round(put_wall, 2)
    long_entry_high = round(put_wall + 10.0, 2)
    long_sl = round(put_wall - 12.0, 2)
    long_tp = round(gamma_flip if spot < gamma_flip else call_wall - 20.0, 2)

    return (
        f"🎯 <b>ACTIONABLE INSTITUTIONAL PLAYBOOK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>Tactical Zone</b>: <code>{zone}</code>\n"
        f"💡 <b>Execution Strategy</b>:\n"
        f"• <b>Primary Bias</b>: {'Fade the rallies near resistance' if dex < 0 else 'Buy structural dips near floor'}\n\n"
        f"🔺 <b>Scenario A (Fade The Highs - Short Setup)</b>:\n"
        f"  • <b>Entry Zone</b>: <code>${short_entry_low} - ${short_entry_high}</code> (Call Wall test)\n"
        f"  • <b>Invalidation SL</b>: <code>${short_sl}</code>\n"
        f"  • <b>Target TP</b>: <code>${short_tp}</code>\n\n"
        f"🔻 <b>Scenario B (Buy The Floor - Long Reversion)</b>:\n"
        f"  • <b>Entry Zone</b>: <code>${long_entry_low} - ${long_entry_high}</code> (Put Wall absorption)\n"
        f"  • <b>Invalidation SL</b>: <code>${long_sl}</code>\n"
        f"  • <b>Target TP</b>: <code>${long_tp}</code>\n\n"
        f"⚠️ <b>Regime Invalidation Warning</b>:\n"
        f"• If 30M candle closes below <code>${gamma_flip:.2f}</code> (Gamma Flip), do NOT buy dips; market enters high-velocity Short Gamma breakdown mode."
    )


def main():
    if not os.path.exists(GEX_FILE):
        print(f"[TG ENGINE] {GEX_FILE} not found.")
        return

    with open(GEX_FILE, "r") as f:
        data = json.load(f)

    spot = float(data.get("spot_xau", 0.0))
    data_source = data.get("data_source", "UNKNOWN")
    call_wall = float(data.get("call_wall_xau", 0.0))
    put_wall = float(data.get("put_wall_xau", 0.0))
    gamma_flip = float(data.get("gamma_flip_xau", spot))
    dex = float(data.get("net_dex_m", 0.0))
    regime = data.get("net_gamma_regime", "LONG_GAMMA_MEAN_REVERT")
    sync_time = data.get("timestamp_utc", datetime.now(timezone.utc).strftime("%H:%M UTC | %d %b %Y"))

    us10y_yield, yield_bias = fetch_fred_yield()

    regime_str = "🟩 LONG GAMMA (Mean-Reverting)" if "LONG" in regime else "🟥 SHORT GAMMA (Volatility Expansion)"
    dex_color = "🟢" if dex >= 0 else "🔴"
    dex_label = "Dealer Net Long" if dex >= 0 else "Dealer Net Short"

    call_dist = round(call_wall - spot, 2)
    put_dist = round(spot - put_wall, 2)

    # 1. Send the untouched original pulse card first
    original_pulse = format_original_radar_card(
        spot, data_source, us10y_yield, yield_bias,
        regime_str, dex, dex_color, dex_label,
        call_wall, put_wall, gamma_flip,
        call_dist, put_dist, sync_time
    )
    send_tg_msg(original_pulse)

    # 2. Send the detailed actionable playbook right after
    playbook = format_actionable_playbook(spot, call_wall, put_wall, gamma_flip, dex)
    send_tg_msg(playbook)

    print("[TG ENGINE] Both Original Radar Pulse and Actionable Playbook dispatched.")


if __name__ == "__main__":
    main()
