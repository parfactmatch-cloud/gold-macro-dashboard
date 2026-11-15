"""
live_telegram_listener.py
Ultra-lightweight 24/7 Telegram Command Listener for Botkeep
RAM usage: ~25 MB | Zero heavy dependencies (requests only)
"""

import os
import time
import json
import logging
import requests

from telegram_commands import fetch_new_updates, log_unauthorized

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# GitHub raw URL jahan se latest levels aur trades fetch honge
# Apne github username aur repo name ke hisab se set karein
GITHUB_RAW_BASE = os.getenv("GITHUB_RAW_BASE", "https://raw.githubusercontent.com/parfactmatch-cloud/gold-macro-dashboard/main")


def send_tg_msg(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        logger.warning("[TG POST ERROR] %s", e)


def fetch_remote_json(filename: str) -> dict:
    url = f"{GITHUB_RAW_BASE}/{filename}?t={int(time.time())}"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return {}


def handle_commands():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("[ERROR] TELEGRAM_BOT_TOKEN missing!")
        return

    logger.info("🤖 Live 24/7 Telegram Listener Started on Botkeep...")
    logger.info(
        "Using shared Telegram offset (telegram_commands.py): commands are "
        "answered exactly once across this listener and telegram_engine.py."
    )

    while True:
        try:
            # Shared offset: updates already consumed by telegram_engine.py
            # are not re-answered here, and vice versa.
            for update, authorized in fetch_new_updates():
                if not authorized:
                    log_unauthorized(update)
                    continue
                msg = update.get("message", {})
                text = msg.get("text", "").strip().lower()

                # /levels Command
                if text == "/levels":
                    gex = fetch_remote_json("gex_levels.json")
                    spot = float(gex.get("spot_xau", 0.0))
                    call_w = float(gex.get("call_wall_xau", 0.0))
                    put_w = float(gex.get("put_wall_xau", 0.0))
                    flip = float(gex.get("gamma_flip_xau", 0.0))
                    of = gex.get("order_flow", {})
                    poc = float(of.get("session_poc", spot))
                    vah = float(of.get("session_vah", spot + 15.0))
                    val = float(of.get("session_val", spot - 15.0))

                    reply = (
                        f"⚡ <b>INSTANT QUANTITATIVE LEVELS</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💵 <b>Current Spot</b>: <code>${spot:.2f}</code>\n"
                        f"🧱 <b>Call Wall</b>: <code>${call_w:.2f}</code> (+${call_w - spot:.2f})\n"
                        f"🧱 <b>Put Wall</b>: <code>${put_w:.2f}</code> (-${spot - put_w:.2f})\n"
                        f"⚡ <b>Gamma Flip</b>: <code>${flip:.2f}</code>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 <b>Session Profile</b>:\n"
                        f"• <b>VAH</b>: <code>${vah:.2f}</code>\n"
                        f"• <b>POC</b>: <code>${poc:.2f}</code>\n"
                        f"• <b>VAL</b>: <code>${val:.2f}</code>"
                    )
                    if gex.get("synthetic"):
                        reply += "\n⚠️ <b>Levels are SYNTHETIC estimates, not live data.</b>"
                    send_tg_msg(reply)

                # /kronos or /ai Command (momentum regime check, not a neural model)
                elif text in ["/kronos", "/ai"]:
                    kdata = fetch_remote_json("kronos_signal.json")
                    k_regime = kdata.get("predicted_regime", "CHOP_CONSOLIDATION")
                    k_prob = float(kdata.get("bullish_probability", 0.50))

                    reply = (
                        f"🤖 <b>KRONOS MOMENTUM STATUS (INSTANT)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"• <b>Model</b>: 10-bar momentum sigmoid (not a neural network)\n"
                        f"• <b>Predicted Regime</b>: <code>{k_regime}</code>\n"
                        f"• <b>Bullish Probability</b>: <code>{k_prob*100:.1f}%</code>\n"
                        f"• <b>Bearish Probability</b>: <code>{(1.0-k_prob)*100:.1f}%</code>\n"
                        f"• <b>Gatekeeper State</b>: <code>{'🛡️ ACTIVE' if 0.42 <= k_prob <= 0.58 else '⚡ PERMISSIVE'}</code>"
                    )
                    send_tg_msg(reply)

                # /pnl or /summary Command
                elif text in ["/pnl", "/summary"]:
                    tdata = fetch_remote_json("paper_trades.json")
                    closed = tdata.get("closed_trades", [])
                    wins = len([t for t in closed if t.get("pnl", 0) > 0])
                    losses = len([t for t in closed if t.get("pnl", 0) < 0])
                    total = len(closed)
                    wr = (wins / total * 100) if total > 0 else 0.0
                    total_pnl = sum([t.get("pnl", 0.0) for t in closed])

                    reply = (
                        f"📊 <b>JEV PERFORMANCE LEDGER (INSTANT)</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💰 <b>Portfolio</b>: <code>${tdata.get('balance', 10000.0):.2f}</code>\n"
                        f"📈 <b>Cumulative PnL</b>: <code>{'+' if total_pnl >= 0 else ''}${total_pnl:.2f}</code>\n"
                        f"🎯 <b>Win Rate</b>: <code>{wr:.1f}%</code> ({wins}W / {losses}L)\n"
                        f"📦 <b>Trades</b>: <code>{total}</code>"
                    )
                    send_tg_msg(reply)

                # /status Command
                elif text == "/status":
                    tdata = fetch_remote_json("paper_trades.json")
                    active = tdata.get("active_trade")
                    if active:
                        reply = (
                            f"⚡ <b>ACTIVE ORDER FLOW POSITION</b>\n"
                            f"• Side: <code>{active['side']} XAU/USD</code>\n"
                            f"• Entry: <code>${active['entry_price']:.2f}</code>\n"
                            f"• Target TP: <code>${active['tp']:.2f}</code>\n"
                            f"• Stop Loss: <code>${active['sl']:.2f}</code>\n"
                            f"• Kronos Momentum Bias: <code>{active.get('kronos_score', 'N/A')}</code>"
                        )
                    else:
                        reply = f"💤 <b>NO ACTIVE TRADES</b>\n• Portfolio: <code>${tdata.get('balance', 10000.0):.2f}</code>"
                    send_tg_msg(reply)

            time.sleep(5)
        except Exception as e:
            logger.warning("Listener error: %s", e)
            time.sleep(3)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    handle_commands()
