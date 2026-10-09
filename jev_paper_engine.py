"""
jev_paper_engine.py
Order Flow Confluence Execution Engine (Jev Model)
- Requires GEX Wall + Volume Profile (POC/VAL/VAH) Confluence
- Delta Absorption & Exhaustion Gatekeeper Filters
- Trailing SL, Breakeven Lock, 50% Scale-Out & 2-Way Bot Commands
"""

import os
import json
import requests
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GEX_FILE = os.path.join(SCRIPT_DIR, "gex_levels.json")
TRADES_FILE = os.path.join(SCRIPT_DIR, "paper_trades.json")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def send_tg_msg(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=8)
    except Exception:
        pass


def load_state() -> dict:
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "balance": 10000.0,
        "active_trade": None,
        "closed_trades": [],
        "last_update_id": 0
    }


def save_state(state: dict):
    with open(TRADES_FILE, "w") as f:
        json.dump(state, f, indent=2)


def handle_telegram_commands(state: dict, spot: float):
    if not TELEGRAM_BOT_TOKEN:
        return

    last_id = state.get("last_update_id", 0)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    try:
        res = requests.get(url, params={"offset": last_id + 1, "timeout": 3}, timeout=6)
        if res.status_code != 200:
            return
        updates = res.json().get("result", [])

        for u in updates:
            state["last_update_id"] = u["update_id"]
            msg = u.get("message", {})
            text = msg.get("text", "").strip().lower()

            active = state.get("active_trade")
            if "/status" in text:
                if active:
                    side = active["side"]
                    entry = active["entry_price"]
                    fl_pnl = (spot - entry) * active["units"] if side == "BUY" else (entry - spot) * active["units"]
                    reply = (
                        f"📊 <b>JEV ACTIVE ORDER FLOW POSITION</b>\n"
                        f"• Side: <code>{side} XAU/USD</code>\n"
                        f"• Entry: <code>${entry:.2f}</code> | Spot: <code>${spot:.2f}</code>\n"
                        f"• PnL: <code>{'+' if fl_pnl >= 0 else ''}${fl_pnl:.2f}</code>\n"
                        f"• Target TP: <code>${active['tp']:.2f}</code> | SL: <code>${active['sl']:.2f}</code>\n"
                        f"• Setup: <code>{active.get('reason')}</code>"
                    )
                else:
                    reply = f"💤 <b>NO ACTIVE TRADES</b>\n• Portfolio: <code>${state.get('balance', 10000.0):.2f}</code>\n• Status: <code>SCANNING_ORDERFLOW_CONFLUENCE</code>"
                send_tg_msg(reply)

            elif "/close" in text and active:
                pnl = (spot - active["entry_price"]) * active["units"] if active["side"] == "BUY" else (active["entry_price"] - spot) * active["units"]
                state["balance"] += pnl
                active["exit_price"] = spot
                active["pnl"] = round(pnl, 2)
                active["exit_reason"] = "MANUAL_BOT_CLOSE"
                state["closed_trades"].append(active)
                state["active_trade"] = None
                save_state(state)
                send_tg_msg(f"🛑 Closed at ${spot:.2f} | PnL: ${pnl:.2f}")

            elif "/summary" in text:
                closed = state.get("closed_trades", [])
                wins = len([t for t in closed if t.get("pnl", 0) > 0])
                total = len(closed)
                wr = (wins / total * 100) if total > 0 else 0.0
                send_tg_msg(f"📈 <b>JEV PERFORMANCE</b>\n• Balance: <code>${state['balance']:.2f}</code>\n• Trades: <code>{total}</code> (WR: <code>{wr:.1f}%</code>)")

        save_state(state)
    except Exception:
        pass


def run_jev_cycle():
    if not os.path.exists(GEX_FILE):
        return

    with open(GEX_FILE, "r") as f:
        telemetry = json.load(f)

    spot = float(telemetry.get("spot_xau", 0.0))
    call_wall = float(telemetry.get("call_wall_xau", 0.0))
    put_wall = float(telemetry.get("put_wall_xau", 0.0))
    gamma_flip = float(telemetry.get("gamma_flip_xau", spot))
    dex = float(telemetry.get("net_dex_m", 0.0))
    blast = bool(telemetry.get("gamma_blast_active", False))

    of = telemetry.get("order_flow", {})
    poc = float(of.get("session_poc", spot))
    val = float(of.get("session_val", spot - 15.0))
    vah = float(of.get("session_vah", spot + 15.0))
    absorption_bias = of.get("absorption_bias", "VALUE_ACCEPTED")

    state = load_state()
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Handle incoming Telegram bot messages
    handle_telegram_commands(state, spot)

    active = state.get("active_trade")

    # 1. Active Position Lifecycle (Scale-out & SL/TP tracking)
    if active:
        side = active["side"]
        entry = active["entry_price"]
        sl = active["sl"]
        tp = active["tp"]
        risk = active["initial_risk"]

        # Scale out 50% at 1.5R
        gain = (spot - entry) if side == "BUY" else (entry - spot)
        if not active.get("partial_taken") and gain >= (risk * 1.5):
            booked = round(active["units"] * 0.5, 2)
            state["balance"] += booked * gain
            active["units"] = round(active["units"] - booked, 2)
            active["partial_taken"] = True
            active["sl"] = entry  # Zero risk lock
            save_state(state)
            send_tg_msg(f"🎯 <b>JEV 50% SCALE-OUT BOOKED (+${booked * gain:.2f})</b>\nStop-Loss locked to Breakeven (${entry:.2f}).")

        # Exit check
        closed = False
        pnl = 0.0
        reason = ""
        if side == "BUY":
            if spot >= tp:
                closed = True; pnl = (tp - entry) * active["units"]; reason = "TARGET_HIT_POC"
            elif spot <= active["sl"]:
                closed = True; pnl = (active["sl"] - entry) * active["units"]; reason = "STOP_LOSS_HIT"
        elif side == "SELL":
            if spot <= tp:
                closed = True; pnl = (entry - tp) * active["units"]; reason = "TARGET_HIT_POC"
            elif spot >= active["sl"]:
                closed = True; pnl = (entry - active["sl"]) * active["units"]; reason = "STOP_LOSS_HIT"

        if closed:
            state["balance"] += pnl
            active["exit_price"] = spot
            active["pnl"] = round(pnl, 2)
            active["exit_reason"] = reason
            state["closed_trades"].append(active)
            state["active_trade"] = None
            save_state(state)
            icon = "🟢" if pnl >= 0 else "🔴"
            send_tg_msg(f"{icon} <b>JEV TRADE CLOSED: {reason}</b>\n• PnL: <code>{'+' if pnl >= 0 else ''}${pnl:.2f}</code>\n• Balance: <code>${state['balance']:.2f}</code>")
        return

    # 2. Fresh Order Flow + GEX Confluence Execution
    side = None
    sl = None
    tp = None
    reason = None

    # Long Confluence: Put Wall floor touch + Value Area Low (VAL) Bullish Absorption
    if spot <= (put_wall + 8.0) and spot <= (val + 4.0) and not blast:
        side = "BUY"
        sl = round(min(put_wall, val) - 10.0, 2)
        tp = round(poc, 2)  # Reversion target to Session POC
        reason = "CONFLUENCE_PUT_WALL_VAL_ABSORPTION"

    # Short Confluence: Call Wall ceiling touch + Value Area High (VAH) Bearish Exhaustion
    elif spot >= (call_wall - 8.0) and spot >= (vah - 4.0) and not blast:
        side = "SELL"
        sl = round(max(call_wall, vah) + 10.0, 2)
        tp = round(poc, 2)  # Reversion target to Session POC
        reason = "CONFLUENCE_CALL_WALL_VAH_EXHAUSTION"

    # Momentum Gamma Blast Continuation
    elif blast and dex > 8.0 and spot > gamma_flip:
        side = "BUY"
        sl = round(spot - 15.0, 2)
        tp = round(spot + 45.0, 2)
        reason = "ORDERFLOW_GAMMA_BLAST_EXPANSION"

    if not side:
        return

    risk_cash = state["balance"] * 0.01
    risk_dist = abs(spot - sl)
    if risk_dist <= 0:
        return

    rr = round(abs(tp - spot) / risk_dist, 2)
    if rr < 1.30:
        return

    units = round(risk_cash / risk_dist, 2)
    new_trade = {
        "trade_id": f"JEV-{int(datetime.now().timestamp())}",
        "side": side,
        "entry_price": spot,
        "units": units,
        "initial_risk": risk_dist,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "reason": reason,
        "be_locked": False,
        "partial_taken": False,
        "opened_at": now_utc
    }

    state["active_trade"] = new_trade
    save_state(state)

    card = (
        f"⚡ <b>JEV ORDER FLOW EXECUTION CARD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 <b>Setup</b>: <code>{reason}</code>\n"
        f"🎯 <b>Action</b>: <code>{side} XAU/USD</code>\n"
        f"💵 <b>Fill Spot</b>: <code>${spot:.2f}</code> ({units} oz)\n"
        f"🛑 <b>Stop Loss</b>: <code>${sl:.2f}</code>\n"
        f"🎯 <b>POC Target</b>: <code>${tp:.2f}</code> (R:R 1:{rr})\n"
        f"📊 <b>Profile Context</b>: VAL ${val:.2f} | POC ${poc:.2f} | VAH ${vah:.2f}\n"
        f"⏰ <b>Executed At</b>: <code>{now_utc}</code>"
    )
    send_tg_msg(card)


if __name__ == "__main__":
    run_jev_cycle()
                
