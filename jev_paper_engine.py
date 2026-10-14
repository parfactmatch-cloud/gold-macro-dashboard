    """
jev_paper_engine.py
Order Flow Confluence Execution Engine (Jev Model)
- Integrated with Kronos K-Line Foundation Model Neural Gatekeeper
- Integrated with High-Impact Macro Economic News Blackout Gatekeeper
- Integrated with LBMA Benchmark Auction Timing Engine (IST Native)
- Integrated with L2/L3 DOM Imbalance & CVD Divergence Microstructure Engine
- Integrated with Remote Telegram Control Center (/pause & /resume support)
- Dynamic Volatility Scaling: Live 14-period 5M ATR Dynamic Stop-Loss Buffer
- Short Gamma Breakdown Execution: Captures rapid downside liquidation cascades
- Hardened SL/TP Sanity Checks (Prevents Inverted SL on Longs/Shorts)
"""

import os
import json
import math
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

# Import micro-engines
try:
    from order_flow_dom_engine import get_order_flow_telemetry
except ImportError:
    get_order_flow_telemetry = None

try:
    from lbma_benchmark_engine import inspect_lbma_auction_window
except ImportError:
    inspect_lbma_auction_window = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GEX_FILE = os.path.join(SCRIPT_DIR, "gex_levels.json")
TRADES_FILE = os.path.join(SCRIPT_DIR, "paper_trades.json")
KRONOS_FILE = os.path.join(SCRIPT_DIR, "kronos_signal.json")
NEWS_FILE = os.path.join(SCRIPT_DIR, "news_status.json")
CONTROL_FILE = os.path.join(SCRIPT_DIR, "bot_control.json")

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


def compute_dynamic_atr_buffer(ticker_symbol: str = "GC=F") -> float:
    """Calculates 14-period ATR on 5M timeframe for dynamic stop buffer."""
    try:
        t = yf.Ticker(ticker_symbol)
        df = t.history(period="1d", interval="5m")
        if df.empty or len(df) < 15:
            return 8.50

        high = df["High"]
        low = df["Low"]
        close_prev = df["Close"].shift(1)

        tr = pd.concat([
            high - low,
            (high - close_prev).abs(),
            (low - close_prev).abs()
        ], axis=1).max(axis=1)

        atr = tr.rolling(window=14).mean().iloc[-1]
        if math.isnan(atr) or atr <= 0:
            return 8.50

        adaptive_buffer = round(float(atr * 1.25), 2)
        return max(4.50, min(adaptive_buffer, 18.00))
    except Exception:
        return 8.50


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
                        f"• Dynamic Buffer: <code>${active.get('atr_buffer', 8.50):.2f}</code>\n"
                        f"• DOM / CVD: <code>{active.get('order_flow_flow', 'N/A')}</code>\n"
                        f"• Neural AI Score: <code>{active.get('kronos_score', 'N/A')}</code>\n"
                        f"• Setup: <code>{active.get('reason')}</code>"
                    )
                else:
                    reply = (
                        f"💤 <b>NO ACTIVE TRADES</b>\n"
                        f"• Portfolio: <code>${state.get('balance', 10000.0):.2f}</code>\n"
                        f"• Status: <code>SCANNING_GEX_DOM_LBMA_CONFLUENCE</code>"
                    )
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
                total_pnl = sum([t.get("pnl", 0.0) for t in closed])
                send_tg_msg(
                    f"📈 <b>JEV QUANTITATIVE PERFORMANCE</b>\n"
                    f"• Balance: <code>${state['balance']:.2f}</code>\n"
                    f"• Cumulative PnL: <code>{'+' if total_pnl >= 0 else ''}${total_pnl:.2f}</code>\n"
                    f"• Total Trades: <code>{total}</code> (WR: <code>{wr:.1f}%</code>)"
                )

        save_state(state)
    except Exception:
        pass


def run_jev_cycle():
    if not os.path.exists(GEX_FILE):
        return

    with open(GEX_FILE, "r") as f:
        telemetry = json.load(f)

    spot = float(telemetry.get("spot_xau", 0.0))
    if spot <= 1000 or math.isnan(spot):
        return

    call_wall = float(telemetry.get("call_wall_xau", 0.0))
    put_wall = float(telemetry.get("put_wall_xau", 0.0))
    gamma_flip = float(telemetry.get("gamma_flip_xau", spot))
    dex = float(telemetry.get("net_dex_m", 0.0))
    blast = bool(telemetry.get("gamma_blast_active", False))

    of = telemetry.get("order_flow", {})
    poc = float(of.get("session_poc", spot))
    val = float(of.get("session_val", spot - 15.0))
    vah = float(of.get("session_vah", spot + 15.0))

    # 1. Ingest L2/L3 DOM & CVD Order Flow Telemetry
    dom_bias = "NEUTRAL"
    imbalance_ratio = 1.0
    cvd_divergence = "NONE"
    cvd_trend = "FLAT"
    atr_buffer = compute_dynamic_atr_buffer()

    if get_order_flow_telemetry:
        try:
            of_telemetry = get_order_flow_telemetry()
            dom_metrics = of_telemetry.get("dom_metrics", {})
            dom_bias = dom_metrics.get("dom_bias", "NEUTRAL")
            imbalance_ratio = dom_metrics.get("imbalance_ratio", 1.0)
            atr_buffer = dom_metrics.get("atr_buffer", atr_buffer)
            cvd_data = dom_metrics.get("cvd_telemetry", {})
            cvd_divergence = cvd_data.get("cvd_divergence", "NONE")
            cvd_trend = cvd_data.get("cvd_trend", "FLAT")
        except Exception as e:
            print(f"[DOM TELEMETRY EXCEPTION] {e}")

    # 2. Ingest LBMA Benchmark Auction Windows (IST Native)
    lbma_policy = "NORMAL"
    lbma_phase = "CONTINUOUS_TRADING"
    lbma_risk_mult = 1.0
    lbma_rr_mult = 1.0
    if inspect_lbma_auction_window:
        try:
            lbma_status = inspect_lbma_auction_window()
            lbma_policy = lbma_status.get("execution_policy", "NORMAL")
            lbma_phase = lbma_status.get("phase", "CONTINUOUS_TRADING")
            lbma_risk_mult = lbma_status.get("risk_weight", 1.0)
            lbma_rr_mult = lbma_status.get("rr_multiplier", 1.0)
        except Exception as e:
            print(f"[LBMA RADAR EXCEPTION] {e}")

    # 3. Ingest Kronos Signals
    kronos_bull_prob = 0.50
    kronos_regime = "NEUTRAL"
    if os.path.exists(KRONOS_FILE):
        try:
            with open(KRONOS_FILE, "r") as kf:
                kdata = json.load(kf)
                kronos_bull_prob = float(kdata.get("bullish_probability", 0.50))
                kronos_regime = kdata.get("predicted_regime", "NEUTRAL")
        except Exception:
            pass

    # 4. Ingest Macro High-Impact News Blackout Status
    news_blackout = False
    event_name = ""
    if os.path.exists(NEWS_FILE):
        try:
            with open(NEWS_FILE, "r") as nf:
                ndata = json.load(nf)
                news_blackout = bool(ndata.get("blackout_active", False))
                event_name = ndata.get("event_name", "High Impact USD Event")
        except Exception:
            pass

    state = load_state()
    now_utc = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Handle incoming Telegram commands
    handle_telegram_commands(state, spot)

    active = state.get("active_trade")

    # Active Position Lifecycle
    if active:
        side = active["side"]
        entry = active["entry_price"]
        risk = active["initial_risk"]

        # News Shield Activation
        if news_blackout and not active.get("be_locked"):
            active["sl"] = entry
            active["be_locked"] = True
            save_state(state)
            send_tg_msg(
                f"🛡️ <b>NEWS SHIELD ACTIVATED</b>\n"
                f"• Event: <code>{event_name}</code>\n"
                f"• Stop-Loss Auto-Locked to Breakeven (${entry:.2f})"
            )

        # 50% Scale-out at 1.5R
        gain = (spot - entry) if side == "BUY" else (entry - spot)
        if not active.get("partial_taken") and gain >= (risk * 1.5):
            booked = round(active["units"] * 0.5, 2)
            state["balance"] += booked * gain
            active["units"] = round(active["units"] - booked, 2)
            active["partial_taken"] = True
            active["sl"] = entry
            save_state(state)
            send_tg_msg(f"🎯 <b>JEV 50% SCALE-OUT BOOKED (+${booked * gain:.2f})</b>\nStop-Loss locked to Breakeven (${entry:.2f}).")

        # Settlement check
        closed = False
        pnl = 0.0
        reason = ""
        if side == "BUY":
            if spot >= active["tp"]:
                closed = True; pnl = (active["tp"] - entry) * active["units"]; reason = "TARGET_HIT_POC"
            elif spot <= active["sl"]:
                closed = True; pnl = (active["sl"] - entry) * active["units"]; reason = "STOP_LOSS_HIT"
        elif side == "SELL":
            if spot <= active["tp"]:
                closed = True; pnl = (entry - active["tp"]) * active["units"]; reason = "TARGET_HIT_POC"
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

    # Master Execution Gatekeepers
    if news_blackout:
        print(f"[JEV ENGINE] Execution blocked: News Blackout Active ({event_name}).")
        return

    if lbma_policy == "TOTAL_EXECUTION_BLACKOUT":
        print(f"[JEV ENGINE] Execution blocked: Live LBMA Benchmark Auction in progress ({lbma_phase}).")
        return

    if os.path.exists(CONTROL_FILE):
        try:
            with open(CONTROL_FILE, "r") as cf:
                if json.load(cf).get("is_paused", False):
                    print("[JEV ENGINE] Execution blocked: Engine paused via Telegram /pause.")
                    return
        except Exception:
            pass

    # Confluence Setup Identification
    side = None
    sl = None
    tp = None
    reason = None

    # Setup 1: Short Gamma Breakdown Cascade (Floor & Gamma Flip Breached)
    if spot < put_wall and spot < gamma_flip:
        side = "SELL"
        sl = round(min(put_wall + atr_buffer, gamma_flip + 2.0), 2)
        # Expansion target projecting short gamma momentum
        tp = round(spot - (atr_buffer * 3.5), 2)
        reason = "SHORT_GAMMA_WALL_BREAKDOWN_EXPANSION"

    # Setup 2: Long Reversion (Put Wall or VAL Absorption in Non-Blast Mode)
    elif (spot <= (put_wall + 8.0) or spot <= (val + 4.0)) and not blast:
        # Invalidation Guard: agar spot gamma_flip ke niche slice ho chuka hai to bounce buy block
        if spot < gamma_flip:
            print("[JEV GATE] Long setup skipped: Spot is below Gamma Neutral Flip (Breakdown danger).")
        else:
            target_tp = round(poc, 2)
            if target_tp > spot + 4.0:
                side = "BUY"
                floor_ref = min(spot, put_wall, val)
                sl = round(floor_ref - atr_buffer, 2)
                tp = target_tp
                reason = "CONFLUENCE_PUT_WALL_VAL_ABSORPTION"

    # Setup 3: Short Reversion (Call Wall or VAH Exhaustion in Non-Blast Mode)
    elif (spot >= (call_wall - 8.0) or spot >= (vah - 4.0)) and not blast:
        target_tp = round(poc, 2)
        if target_tp < spot - 4.0:
            side = "SELL"
            ceil_ref = max(spot, call_wall, vah)
            sl = round(ceil_ref + atr_buffer, 2)
            tp = target_tp
            reason = "CONFLUENCE_CALL_WALL_VAH_EXHAUSTION"

    # Setup 4: Bullish Gamma Blast Expansion (Upside Surge)
    elif blast and abs(dex) > 8.0 and spot > gamma_flip:
        side = "BUY"
        sl = round(spot - (atr_buffer * 1.5), 2)
        tp = round(spot + (atr_buffer * 3.5), 2)
        reason = "ORDERFLOW_GAMMA_BLAST_EXPANSION"

    if not side or not sl or not tp:
        return

    # LBMA Pre-Fix Inventory Shading Guard
    if lbma_policy == "BLOCK_MEAN_REVERSION_MOMENTUM_ONLY" and "ABSORPTION" in reason:
        print(f"[JEV LBMA GATE] Fade rejected: Pre-Fix directional inventory ramp in progress ({lbma_phase}).")
        return

    # L2/L3 DOM Imbalance & CVD Divergence Gatekeepers
    if side == "BUY":
        if dom_bias == "INSTITUTIONAL_OFFER_ABSORPTION":
            print(f"[JEV DOM GATE] BUY rejected: Strong Offer Absorption (IR: {imbalance_ratio})")
            return
        if cvd_divergence == "BEARISH_EXHAUSTION_DIV":
            print("[JEV CVD GATE] BUY rejected: Bearish CVD Exhaustion Divergence active.")
            return
        if cvd_divergence == "BULLISH_ABSORPTION_DIV":
            reason += "_CVD_ABSORBED"

    if side == "SELL":
        if dom_bias == "INSTITUTIONAL_BID_ABSORPTION" and "BREAKDOWN" not in reason:
            print(f"[JEV DOM GATE] SELL rejected: Strong Bid Absorption (IR: {imbalance_ratio})")
            return
        if cvd_divergence == "BULLISH_ABSORPTION_DIV":
            print("[JEV CVD GATE] SELL rejected: Bullish CVD Absorption Divergence active.")
            return
        if cvd_divergence == "BEARISH_EXHAUSTION_DIV":
            reason += "_CVD_EXHAUSTED"

    # Hard Directional SL Sanity Guard
    if side == "BUY" and sl >= spot:
        return
    if side == "SELL" and sl <= spot:
        return

    # Kronos Neural Confidence Filter
    # Breakdown setups are aggressive momentum trades; require bear probability >= 0.40
    if reason.startswith("SHORT_GAMMA_WALL_BREAKDOWN"):
        if kronos_bull_prob > 0.65:
            print(f"[JEV GATEKEEPER] Breakdown SELL rejected: Kronos high bullish score ({kronos_bull_prob:.2f})")
            return
    else:
        if side == "BUY" and kronos_bull_prob < 0.40:
            print(f"[JEV GATEKEEPER] BUY Setup rejected: Kronos Bearish Bias ({kronos_bull_prob:.2f})")
            return
        if side == "SELL" and kronos_bull_prob > 0.60:
            print(f"[JEV GATEKEEPER] SELL Setup rejected: Kronos Bullish Bias ({kronos_bull_prob:.2f})")
            return

    # Probabilistic Fractional Sizing & LBMA Scaling
    base_risk = 0.010
    if (side == "BUY" and kronos_bull_prob >= 0.70) or (side == "SELL" and kronos_bull_prob <= 0.30):
        risk_pct = 0.015
    elif 0.45 <= kronos_bull_prob <= 0.55:
        risk_pct = 0.0075
    else:
        risk_pct = base_risk

    # Apply LBMA Phase Weight
    risk_pct = risk_pct * lbma_risk_mult
    risk_cash = state["balance"] * risk_pct
    risk_dist = abs(spot - sl)
    if risk_dist <= 0:
        return

    # Target Expansion during Post-Fix Elastic Snap-Back
    if lbma_rr_mult > 1.0 and "BREAKDOWN" not in reason:
        expanded_target_dist = abs(tp - spot) * lbma_rr_mult
        tp = round(spot + expanded_target_dist if side == "BUY" else spot - expanded_target_dist, 2)
        reason += f"_{lbma_phase}"

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
        "atr_buffer": atr_buffer,
        "order_flow_flow": f"{dom_bias} (IR {imbalance_ratio}) | CVD: {cvd_divergence}",
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "reason": reason,
        "kronos_score": f"{kronos_regime} ({kronos_bull_prob:.2f})",
        "lbma_phase": lbma_phase,
        "be_locked": False,
        "partial_taken": False,
        "opened_at": now_utc
    }

    state["active_trade"] = new_trade
    save_state(state)

    card = (
        f"⚡ <b>JEV INSTITUTIONAL EXECUTION CARD</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 <b>Setup</b>: <code>{reason}</code>\n"
        f"🎯 <b>Action</b>: <code>{side} XAU/USD</code>\n"
        f"💵 <b>Fill Spot</b>: <code>${spot:.2f}</code> ({units} oz | {risk_pct*100:.2f}% Risk)\n"
        f"🛑 <b>Stop Loss</b>: <code>${sl:.2f}</code> (ATR Dyn Buffer: <code>${atr_buffer:.2f}</code>)\n"
        f"🎯 <b>Target POC</b>: <code>${tp:.2f}</code> (R:R 1:{rr})\n"
        f"🌊 <b>DOM Flow</b>: <code>{dom_bias} (IR: {imbalance_ratio})</code>\n"
        f"📈 <b>CVD State</b>: <code>{cvd_trend} | {cvd_divergence}</code>\n"
        f"🏛 <b>LBMA Phase</b>: <code>{lbma_phase}</code>\n"
        f"🧠 <b>Kronos Neural Gate</b>: <code>{kronos_regime} ({kronos_bull_prob:.2f})</code>\n"
        f"📊 <b>Profile Context</b>: VAL ${val:.2f} | POC ${poc:.2f} | VAH ${vah:.2f}\n"
        f"⏰ <b>Executed At</b>: <code>{now_utc}</code>"
    )
    send_tg_msg(card)


if __name__ == "__main__":
    run_jev_cycle()
