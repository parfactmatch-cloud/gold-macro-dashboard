"""
jev_paper_engine.py
Institutional Quantitative Execution & Paper-Trading Core (Jev Model).
- Two-Way Interactive Telegram Command Listener (/status, /close, /summary)
- Dynamic Trailing SL, Breakeven Shield & Partial Profit Scale-Out
- Institutional GEX Corridor Confluence & Macro Risk Gatekeeper
- Real-time Ledger & Performance Scorecard Engine
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


# =====================================================================
# 1. TWO-WAY TELEGRAM COMMAND LISTENER (/status, /close, /summary)
# =====================================================================
def handle_telegram_commands(state: dict, spot: float):
    if not TELEGRAM_BOT_TOKEN:
        return

    last_id = state.get("last_update_id", 0)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={last_id + 1}&timeout=3"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code != 200:
            return
        updates = res.json().get("result", [])

        for u in updates:
            state["last_update_id"] = u["update_id"]
            msg = u.get("message", {})
            text = msg.get("text", "").strip().lower()

            if not text:
                continue

            active = state.get("active_trade")

            if text == "/status":
                if active:
                    side = active["side"]
                    entry = active["entry_price"]
                    fl_pnl = (spot - entry) * active["units"] if side == "BUY" else (entry - spot) * active["units"]
                    reply = (
                        f"📊 <b>JEV ACTIVE POSITION STATUS</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"• <b>Side</b>: <code>{side} XAU/USD</code>\n"
                        f"• <b>Entry Spot</b>: <code>${entry:.2f}</code>\n"
                        f"• <b>Live Spot</b>: <code>${spot:.2f}</code>\n"
                        f"• <b>Units</b>: <code>{active['units']} oz</code>\n"
                        f"• <b>Floating PnL</b>: <code>{'+' if fl_pnl >= 0 else ''}${fl_pnl:.2f}</code>\n"
                        f"• <b>Active SL</b>: <code>${active['sl']:.2f}</code>\n"
                        f"• <b>Target TP</b>: <code>${active['tp']:.2f}</code>\n"
                        f"• <b>Shield State</b>: <code>{'BREAKEVEN_LOCKED' if active.get('be_locked') else 'ACTIVE_RISK'}</code>"
                    )
                else:
                    reply = (
                        f"💤 <b>NO ACTIVE JEV POSITION</b>\n"
                        f"• <b>Portfolio Balance</b>: <code>${state['balance']:.2f}</code>\n"
                        f"• <b>Engine State</b>: <code>SCANNING_GEX_WALLS</code>"
                    )
                send_tg_msg(reply)

            elif text == "/close":
                if active:
                    side = active["side"]
                    entry = active["entry_price"]
                    pnl = (spot - entry) * active["units"] if side == "BUY" else (entry - spot) * active["units"]
                    state["balance"] += pnl
                    active["exit_price"] = spot
                    active["pnl"] = round(pnl, 2)
                    active["exit_reason"] = "MANUAL_TELEGRAM_COMMAND_CLOSE"
                    active["closed_at"] = datetime.now(timezone.utc).strftime("%H:%M UTC")
                    state["closed_trades"].append(active)
                    state["active_trade"] = None
                    save_state(state)
                    send_tg_msg(f"🛑 <b>POSITION FORCED CLOSED VIA BOT</b>\n• Realized PnL: <code>${pnl:.2f}</code>\n• Balance: <code>${state['balance']:.2f}</code>")
                else:
                    send_tg_msg("⚠️ No open trade to close.")

            elif text == "/summary":
                closed = state.get("closed_trades", [])
                total_trades = len(closed)
                wins = len([t for t in closed if t.get("pnl", 0.0) > 0])
                losses = len([t for t in closed if t.get("pnl", 0.0) < 0])
                win_rate = (wins / total_trades * 100.0) if total_trades > 0 else 0.0
                total_pnl = sum([t.get("pnl", 0.0) for t in closed])

                summary_card = (
                    f"📈 <b>JEV QUANTITATIVE PERFORMANCE DIGEST</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"• <b>Virtual Balance</b>: <code>${state['balance']:.2f}</code>\n"
                    f"• <b>Net Cumulative PnL</b>: <code>{'+' if total_pnl >= 0 else ''}${total_pnl:.2f}</code>\n"
                    f"• <b>Total Trades</b>: <code>{total_trades}</code>\n"
                    f"• <b>Win/Loss Record</b>: <code>{wins}W - {losses}L</code>\n"
                    f"• <b>Win Rate</b>: <code>{win_rate:.1f}%</code>\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚡ <b>Risk Profile</b>: 1.0% Fixed Risk per Setup"
                )
                send_tg_msg(summary_card)

        save_state(state)
    except Exception as e:
        print(f"[TG LISTENER WARN] {e}")


# =====================================================================
# 2. AUTONOMOUS EXECUTION & TRAILING SL / SCALE-OUT ENGINE
# =====================================================================
def run_jev_cycle():
    if not os.path.exists(GEX_FILE):
        print("[JEV] gex_levels.json not ready.")
        return

    with open(GEX_FILE, "r") as f:
        gex = json.load(f)

    spot = float(gex.get("spot_xau", 0.0))
    call_wall = float(gex.get("call_wall_xau", 0.0))
    put_wall = float(gex.get("put_wall_xau", 0.0))
    gamma_flip = float(gex.get("gamma_flip_xau", spot))
    regime = str(gex.get("net_gamma_regime", "LONG_GAMMA_MEAN_REVERT"))
    dex = float(gex.get("net_dex_m", 0.0))
    blast = bool(gex.get("gamma_blast_active", False))

    state = load_state()
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Step A: Listen to live bot commands first
    handle_telegram_commands(state, spot)

    active = state.get("active_trade")

    # -------------------------------------------------------------
    # Step B: ACTIVE TRADE MANAGEMENT (Trailing SL, Breakeven & TP)
    # -------------------------------------------------------------
    if active:
        side = active["side"]
        entry = active["entry_price"]
        sl = active["sl"]
        tp = active["tp"]
        initial_risk = active["initial_risk"]
        units = active["units"]

        # 1. Partial Scale-Out (50% Position Profit Lock at 1:1.5 R:R)
        current_gain = (spot - entry) if side == "BUY" else (entry - spot)
        if not active.get("partial_taken") and current_gain >= (initial_risk * 1.5):
            booked_units = round(units * 0.5, 2)
            partial_pnl = booked_units * current_gain
            state["balance"] += partial_pnl
            active["units"] = round(units - booked_units, 2)
            active["partial_taken"] = True
            active["be_locked"] = True
            active["sl"] = entry  # Move SL to Entry (Free Trade)
            save_state(state)

            send_tg_msg(
                f"🎯 <b>JEV SCALE-OUT: 50% PROFIT SECURED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Booked Profit</b>: <code>+${partial_pnl:.2f}</code>\n"
                f"• <b>Remaining Position</b>: <code>{active['units']} oz</code>\n"
                f"• <b>Stop Loss Status</b>: <code>Moved to Entry (${entry:.2f}) [ZERO RISK]</code>"
            )

        # 2. Dynamic Trailing Stop-Loss for Remaining Runners
        if active.get("be_locked"):
            if side == "BUY" and spot > (entry + initial_risk * 2.0):
                new_trail = round(spot - initial_risk, 2)
                if new_trail > active["sl"]:
                    active["sl"] = new_trail
                    save_state(state)
            elif side == "SELL" and spot < (entry - initial_risk * 2.0):
                new_trail = round(spot + initial_risk, 2)
                if new_trail < active["sl"]:
                    active["sl"] = new_trail
                    save_state(state)

        # 3. Check Final Exits (SL or Final TP)
        closed = False
        pnl = 0.0
        reason = ""

        if side == "BUY":
            if spot >= tp:
                closed = True
                pnl = (tp - entry) * active["units"]
                reason = "🎯 FINAL RUNNER TARGET HIT"
            elif spot <= active["sl"]:
                closed = True
                pnl = (active["sl"] - entry) * active["units"]
                reason = "🛑 TRAILING SL TRIGGERED" if active.get("be_locked") else "🛑 STRUCTURAL SL HIT"
        elif side == "SELL":
            if spot <= tp:
                closed = True
                pnl = (entry - tp) * active["units"]
                reason = "🎯 FINAL RUNNER TARGET HIT"
            elif spot >= active["sl"]:
                closed = True
                pnl = (entry - active["sl"]) * active["units"]
                reason = "🛑 TRAILING SL TRIGGERED" if active.get("be_locked") else "🛑 STRUCTURAL SL HIT"

        if closed:
            state["balance"] += pnl
            active["exit_price"] = spot
            active["pnl"] = round(pnl, 2)
            active["exit_reason"] = reason
            active["closed_at"] = now_utc
            state["closed_trades"].append(active)
            state["active_trade"] = None
            save_state(state)

            icon = "🟢" if pnl >= 0 else "🔴"
            card = (
                f"{icon} <b>JEV POSITION CLOSED</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Status</b>: <code>{reason}</code>\n"
                f"• <b>Realized Gain/Loss</b>: <code>{'+' if pnl >= 0 else ''}${pnl:.2f}</code>\n"
                f"• <b>Closing Spot</b>: <code>${spot:.2f}</code>\n"
                f"• <b>Virtual Balance</b>: <code>${state['balance']:.2f}</code>\n"
                f"⏰ <b>Timestamp</b>: <code>{now_utc}</code>"
            )
            send_tg_msg(card)
            return

        print(f"[JEV] Active {side} running. Spot: ${spot:.2f} | SL: ${active['sl']:.2f}")
        return

    # -------------------------------------------------------------
    # Step C: EVALUATE FRESH INSTITUTIONAL GEX SETUPS
    # -------------------------------------------------------------
    side = None
    sl = None
    tp = None
    reason = None

    # Setup 1: Put Wall Floor Absorption (Mean Reversion Long)
    if spot <= (put_wall + 6.0) and spot > put_wall and not blast:
        side = "BUY"
        sl = round(put_wall - 12.0, 2)
        tp = round(gamma_flip, 2)
        reason = "JEV_PUT_WALL_ABSORPTION"

    # Setup 2: Call Wall Ceiling Exhaustion (Mean Reversion Short)
    elif spot >= (call_wall - 6.0) and spot < call_wall and not blast:
        side = "SELL"
        sl = round(call_wall + 12.0, 2)
        tp = round(gamma_flip, 2)
        reason = "JEV_CALL_WALL_EXHAUSTION"

    # Setup 3: Gamma Blast Liquidity Expansion (High-Velocity Trend)
    elif blast and dex > 8.0 and spot > gamma_flip:
        side = "BUY"
        sl = round(spot - 15.0, 2)
        tp = round(spot + 40.0, 2)
        reason = "JEV_INSTITUTIONAL_GAMMA_BLAST"

    if not side:
        print("[JEV] Corridor scanning active. No high-probability setup.")
        return

    # Gatekeeper Risk Allocation: 1% Fixed Risk of virtual balance
    risk_cash = state["balance"] * 0.01  # $100 on $10k
    risk_dist = abs(spot - sl)
    if risk_dist <= 0:
        return

    units = round(risk_cash / risk_dist, 2)
    rr = round(abs(tp - spot) / risk_dist, 2)

    if rr < 1.30:
        print(f"[JEV GATEKEEPER] Rejected: Offered R:R 1:{rr} below 1:1.30 standard.")
        return

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
        f"⚡ <b>JEV MODEL QUANT EXECUTION CARD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 <b>Setup Model</b>: <code>{reason}</code>\n"
        f"🎯 <b>Action</b>: <code>{side} XAU/USD</code>\n"
        f"💵 <b>Fill Spot</b>: <code>${spot:.2f}</code> ({units} oz)\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 <b>Structural SL</b>: <code>${sl:.2f}</code>\n"
        f"🎯 <b>Target Liquidity</b>: <code>${tp:.2f}</code> (1:{rr})\n"
        f"🧱 <b>Anchor Wall</b>: <code>${put_wall if side == 'BUY' else call_wall:.2f}</code>\n"
        f"⏰ <b>Execution Epoch</b>: <code>{now_utc}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💼 <b>Portfolio State</b>: <code>${state['balance']:.2f}</code>"
    )
    send_tg_msg(card)
    print(f"[JEV] Executed {side} trade at ${spot:.2f}")


if __name__ == "__main__":
    run_jev_cycle()
