"""
telegram_engine.py
Institutional Telemetry & Alerts Broadcaster for Gold (XAU/USD).
Handles Market Radar Pulses, Execution Cards, and Gatekeeper Rejections.
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

def dispatch_telegram(message: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG WARN] Telegram credentials missing.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        res = requests.post(
            url, 
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, 
            timeout=10
        )
        if res.status_code == 200:
            print("[TG OK] Alert dispatched successfully.")
            return True
        else:
            print(f"[TG ERROR] Telegram API returned {res.status_code}: {res.text}")
            return False
    except Exception as e:
        print(f"[TG ERROR] Exception: {e}")
        return False

def broadcast_execution_card(side: str, spot: float, sl: float, tp: float, regime_tag: str, call_wall="N/A", put_wall="N/A", net_dex="N/A") -> bool:
    risk = abs(spot - sl)
    reward = abs(tp - spot)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    is_blast = "GAMMA_BLAST" in regime_tag or "GAMMA_COLLAPSE" in regime_tag
    icon = "🚀🟢 <b>INSTITUTIONAL GAMMA BLAST (LONG)</b>" if (is_blast and side == "BUY") else (
           "🚀🔴 <b>INSTITUTIONAL GAMMA COLLAPSE (SHORT)</b>" if is_blast else (
           "⚡🟢 <b>QUANT ALPHA LONG EXECUTION</b>" if side == "BUY" else "⚡🔴 <b>QUANT ALPHA SHORT EXECUTION</b>"))

    card = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 <b>Model</b>: <code>{regime_tag}</code>\n"
        f"💵 <b>Entry Spot</b>: <code>${spot:.2f}</code>\n"
        f"📐 <b>Profile</b>: <code>Risk: ${risk:.2f} | Target: ${reward:.2f} (1:{rr})</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 <b>Structural SL</b>: <code>${sl:.2f}</code>\n"
        f"🎯 <b>Liquidity Target</b>: <code>${tp:.2f}</code>\n"
        f"🛡️ <b>Call Wall</b>: <code>${call_wall}</code>\n"
        f"🛡️ <b>Put Wall</b>: <code>${put_wall}</code>\n"
        f"⚖️ <b>Net Delta (DEX)</b>: <code>{net_dex}</code>\n"
        f"⏰ <b>Epoch</b>: <code>{now_utc}</code>"
    )
    return dispatch_telegram(card)

def broadcast_gatekeeper_rejection(reason: str, strategy: str, offered_rr: float, barrier="NONE") -> bool:
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    msg = (
        f"🛑 <b>GATEKEEPER REJECTION</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Strategy</b>: <code>{strategy}</code>\n"
        f"• <b>Block Reason</b>: <code>{reason}</code>\n"
        f"• <b>Offered R:R</b>: <code>1:{offered_rr:.2f}</code> (Min: 1:1.30)\n"
        f"• <b>GEX Barrier</b>: <code>{barrier}</code>\n"
        f"• <b>Timestamp</b>: <code>{now_utc}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ <b>Verdict</b>: Capital Protected"
    )
    return dispatch_telegram(msg)

def broadcast_market_pulse(
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
) -> bool:
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
        print(f"[MAIN] {json_path} not found. Skipping pulse broadcast.")
        exit(0)
        
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
            
        spot = float(data.get("spot_xau", 4300.0))
        c_wall = float(data.get("call_wall_xau", 4500.0))
        p_wall = float(data.get("put_wall_xau", 4100.0))
        g_flip = float(data.get("gamma_flip_xau", spot))
        regime = str(data.get("net_gamma_regime", "LONG_GAMMA_MEAN_REVERT"))
        dex = float(data.get("net_dex_m", 0.0))
        blast = bool(data.get("gamma_blast_active", False))
        t_call = str(data.get("call_distance_telemetry", "Call: N/A"))
        t_put = str(data.get("put_distance_telemetry", "Put: N/A"))
        valid = bool(data.get("is_corridor_valid", True))
        
        # Dispatch Radar Pulse
        print("[MAIN] Firing Institutional Radar Pulse to Telegram...")
        broadcast_market_pulse(
            spot=spot,
            call_wall=c_wall,
            put_wall=p_wall,
            gamma_flip=g_flip,
            regime=regime,
            us10y_yield=4.35,
            us10y_impact="NEUTRAL_CONSOLIDATION",
            net_dex=dex,
            blast_active=blast,
            telemetry_call=t_call,
            telemetry_put=t_put,
            is_corridor_valid=valid
        )
    except Exception as e:
        print(f"[MAIN ERROR] Failed to dispatch radar pulse: {e}")
    
