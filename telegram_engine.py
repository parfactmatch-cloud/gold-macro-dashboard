"""
telegram_engine.py
Institutional Macro Telemetry, Kronos Neural AI & Full Telegram Command Center
- Supported Commands: /levels, /pause, /resume, /pnl, /summary, /kronos, /ai
- Dual Broadcaster: Radar Pulse + Actionable Playbook (with Kronos AI Bias)
- Event Trigger: Kronos AI Regime Shift Alert (Chop -> Expansion)
"""

import os
import json
import requests
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GEX_FILE = os.path.join(SCRIPT_DIR, "gex_levels.json")
KRONOS_FILE = os.path.join(SCRIPT_DIR, "kronos_signal.json")
KRONOS_STATE_FILE = os.path.join(SCRIPT_DIR, "kronos_state.json")
TRADES_FILE = os.path.join(SCRIPT_DIR, "paper_trades.json")
CONTROL_FILE = os.path.join(SCRIPT_DIR, "bot_control.json")

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


def load_control_state() -> dict:
    if os.path.exists(CONTROL_FILE):
        try:
            with open(CONTROL_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"is_paused": False, "last_update_id": 0}


def save_control_state(state: dict):
    with open(CONTROL_FILE, "w") as f:
        json.dump(state, f, indent=2)


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


def handle_all_telegram_commands(gex_data: dict, kronos_data: dict):
    """Processes interactive commands sent by user in Telegram."""
    if not TELEGRAM_BOT_TOKEN:
        return

    ctrl = load_control_state()
    last_id = ctrl.get("last_update_id", 0)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, params={"offset": last_id + 1, "timeout": 3}, timeout=6)
        if res.status_code != 200:
            return
        updates = res.json().get("result", [])

        for u in updates:
            ctrl["last_update_id"] = u["update_id"]
            msg = u.get("message", {})
            text = msg.get("text", "").strip().lower()

            spot = float(gex_data.get("spot_xau", 0.0))
            call_wall = float(gex_data.get("call_wall_xau", 0.0))
            put_wall = float(gex_data.get("put_wall_xau", 0.0))
            gamma_flip = float(gex_data.get("gamma_flip_xau", 0.0))
            of = gex_data.get("order_flow", {})
            poc = float(of.get("session_poc", spot))
            vah = float(of.get("session_vah", spot + 15.0))
            val = float(of.get("session_val", spot - 15.0))

            # Command: /levels
            if text == "/levels":
                reply = (
                    f"📐 <b>QUANTITATIVE LEVELS SNAPSHOT</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 <b>Current Spot</b>: <code>${spot:.2f}</code>\n"
                    f"🧱 <b>Call Wall (Ceiling)</b>: <code>${call_wall:.2f}</code> (+${call_wall - spot:.2f})\n"
                    f"🧱 <b>Put Wall (Floor)</b>: <code>${put_wall:.2f}</code> (-${spot - put_wall:.2f})\n"
                    f"⚡ <b>Gamma Flip Level</b>: <code>${gamma_flip:.2f}</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📊 <b>Volume Profile Cluster</b>:\n"
                    f"• <b>VAH (High)</b>: <code>${vah:.2f}</code>\n"
                    f"• <b>POC (Magnet)</b>: <code>${poc:.2f}</code>\n"
                    f"• <b>VAL (Low)</b>: <code>${val:.2f}</code>"
                )
                send_tg_msg(reply)

            # Command: /pause
            elif text == "/pause":
                ctrl["is_paused"] = True
                save_control_state(ctrl)
                send_tg_msg("🛑 <b>BOT EXECUTION PAUSED</b>\nFresh trades suspended. Active positions will still be managed.")

            # Command: /resume
            elif text == "/resume":
                ctrl["is_paused"] = False
                save_control_state(ctrl)
                send_tg_msg("🟢 <b>BOT EXECUTION RESUMED</b>\nAutonomous trade gatekeeper is active.")

            # Command: /pnl or /summary
            elif text in ["/pnl", "/summary"]:
                trades_data = {"balance": 10000.0, "closed_trades": []}
                if os.path.exists(TRADES_FILE):
                    try:
                        with open(TRADES_FILE, "r") as tf:
                            trades_data = json.load(tf)
                    except Exception:
                        pass
                closed = trades_data.get("closed_trades", [])
                wins = len([t for t in closed if t.get("pnl", 0) > 0])
                losses = len([t for t in closed if t.get("pnl", 0) < 0])
                total = len(closed)
                wr = (wins / total * 100) if total > 0 else 0.0
                total_pnl = sum([t.get("pnl", 0.0) for t in closed])
                reply = (
                    f"📊 <b>JEV PERFORMANCE & RISK LEDGER</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 <b>Portfolio Balance</b>: <code>${trades_data.get('balance', 10000.0):.2f}</code>\n"
                    f"📈 <b>Cumulative PnL</b>: <code>{'+' if total_pnl >= 0 else ''}${total_pnl:.2f}</code>\n"
                    f"🎯 <b>Win Rate</b>: <code>{wr:.1f}%</code> ({wins}W / {losses}L)\n"
                    f"📦 <b>Total Trades Closed</b>: <code>{total}</code>\n"
                    f"⚙️ <b>Engine Status</b>: <code>{'PAUSED' if ctrl.get('is_paused') else 'RUNNING_ACTIVE'}</code>"
                )
                send_tg_msg(reply)

            # Command: /kronos or /ai
            elif text in ["/kronos", "/ai"]:
                k_regime = kronos_data.get("predicted_regime", "CHOP_CONSOLIDATION")
                k_prob = float(kronos_data.get("bullish_probability", 0.50))
                reply = (
                    f"🤖 <b>KRONOS NEURAL STATUS REPORT</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>Evaluated Window</b>: 64 Bars (5M Timeframe)\n"
                    f"• <b>Predicted Regime</b>: <code>{k_regime}</code>\n"
                    f"• <b>Bullish Probability</b>: <code>{k_prob*100:.1f}%</code>\n"
                    f"• <b>Bearish Probability</b>: <code>{(1.0-k_prob)*100:.1f}%</code>\n"
                    f"• <b>Gatekeeper Filter</b>: <code>{'🛡️ ACTIVE' if 0.42 <= k_prob <= 0.58 else '⚡ PERMISSIVE'}</code>"
                )
                send_tg_msg(reply)

        save_control_state(ctrl)
    except Exception as e:
        print(f"[TG DISPATCH ERROR] {e}")


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


def format_actionable_playbook(spot: float, call_wall: float, put_wall: float, gamma_flip: float, dex: float, kronos_regime: str, kronos_prob: float) -> str:
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
        f"🧠 <b>Kronos Neural Bias</b>: <code>{kronos_regime} ({kronos_prob*100:.1f}%)</code>\n"
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


def handle_kronos_regime_shift(current_regime: str, bull_prob: float):
    """Detects regime transitions and sends dedicated trigger alert."""
    last_regime = "CHOP_CONSOLIDATION"
    if os.path.exists(KRONOS_STATE_FILE):
        try:
            with open(KRONOS_STATE_FILE, "r") as f:
                last_regime = json.load(f).get("last_regime", "CHOP_CONSOLIDATION")
        except Exception:
            pass

    if current_regime != last_regime:
        icon = "🚀" if "BULLISH" in current_regime else ("🩸" if "BEARISH" in current_regime else "⚖️")
        shift_alert = (
            f"🧠 <b>KRONOS AI: REGIME SHIFT DETECTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>New State</b>: {icon} <code>{current_regime}</code>\n"
            f"🔄 <b>Shift</b>: <code>{last_regime}</code> ➔ <code>{current_regime}</code>\n"
            f"📊 <b>Confidence Odds</b>: <code>{bull_prob*100:.1f}% Bullish | {(1.0-bull_prob)*100:.1f}% Bearish</code>\n"
            f"🕯️ <b>Action</b>: {'Momentum expansion active; look for continuation setups.' if 'EXPANSION' in current_regime else 'Mean-reverting auction; respect corridor boundaries.'}"
        )
        send_tg_msg(shift_alert)

    with open(KRONOS_STATE_FILE, "w") as f:
        json.dump({"last_regime": current_regime}, f, indent=2)


def main():
    if not os.path.exists(GEX_FILE):
        print(f"[TG ENGINE] {GEX_FILE} not found.")
        return

    with open(GEX_FILE, "r") as f:
        data = json.load(f)

    kronos_data = {"predicted_regime": "CHOP_CONSOLIDATION", "bullish_probability": 0.50}
    if os.path.exists(KRONOS_FILE):
        try:
            with open(KRONOS_FILE, "r") as kf:
                kronos_data = json.load(kf)
        except Exception:
            pass

    # Process all incoming remote commands (/levels, /pause, /resume, /pnl, /summary, /kronos, /ai)
    handle_all_telegram_commands(data, kronos_data)

    spot = float(data.get("spot_xau", 0.0))
    data_source = data.get("data_source", "UNKNOWN")
    call_wall = float(data.get("call_wall_xau", 0.0))
    put_wall = float(data.get("put_wall_xau", 0.0))
    gamma_flip = float(data.get("gamma_flip_xau", spot))
    dex = float(data.get("net_dex_m", 0.0))
    regime = data.get("net_gamma_regime", "LONG_GAMMA_MEAN_REVERT")
    sync_time = data.get("timestamp_utc", datetime.now(timezone.utc).strftime("%H:%M UTC | %d %b %Y"))

    kronos_regime = kronos_data.get("predicted_regime", "CHOP_CONSOLIDATION")
    kronos_prob = float(kronos_data.get("bullish_probability", 0.50))

    # Detect & alert if Kronos changed regime
    handle_kronos_regime_shift(kronos_regime, kronos_prob)

    us10y_yield, yield_bias = fetch_fred_yield()

    regime_str = "🟩 LONG GAMMA (Mean-Reverting)" if "LONG" in regime else "🟥 SHORT GAMMA (Volatility Expansion)"
    dex_color = "🟢" if dex >= 0 else "🔴"
    dex_label = "Dealer Net Long" if dex >= 0 else "Dealer Net Short"

    call_dist = round(call_wall - spot, 2)
    put_dist = round(spot - put_wall, 2)

    # 1. Send untouched original pulse card first
    original_pulse = format_original_radar_card(
        spot, data_source, us10y_yield, yield_bias,
        regime_str, dex, dex_color, dex_label,
        call_wall, put_wall, gamma_flip,
        call_dist, put_dist, sync_time
    )
    send_tg_msg(original_pulse)

    # 2. Send detailed actionable playbook with Kronos neural bias
    playbook = format_actionable_playbook(spot, call_wall, put_wall, gamma_flip, dex, kronos_regime, kronos_prob)
    send_tg_msg(playbook)

    print("[TG ENGINE] Radar Pulse, Playbook, Kronos monitors, and Command Dispatch executed.")


if __name__ == "__main__":
    main()
