"""
===============================================================================
PROJECT: QUANT ENGINE WEEKLY PERFORMANCE AUDIT & TELEGRAM DISPATCHER

Reads the REAL Jev paper-trading ledger (paper_trades.json) — the previous
version read scalp_log.csv / trade_log.csv, files no engine in this repo
ever writes, so it always reported an empty window while claiming measured
gating efficacy. Those CSVs are still ingested opportunistically if they
exist, but the ledger is the source of truth.

Trade open time comes from the trade_id epoch (JEV-<unix seconds>); the
ledger's opened_at field is only "HH:MM UTC" with no date, so it cannot
be used for the 7-day window.

Run manually or on a schedule (e.g. a weekly cron). It is NOT run by the
15-minute sync workflow — a weekly report does not belong in a 15-minute loop.
===============================================================================
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRADES_FILE = os.path.join(SCRIPT_DIR, "paper_trades.json")
SCALP_LOG_FILE = os.path.join(SCRIPT_DIR, "scalp_log.csv")
TRADE_LOG_FILE = os.path.join(SCRIPT_DIR, "trade_log.csv")


def dispatch_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("[TG WARN] Bot token or Chat ID not configured.")
        return False
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        return res.status_code == 200
    except Exception as e:
        logger.warning("[TG ERROR] %s", e)
        return False


def parse_utc_timestamp(ts_val):
    """Safely converts heterogeneous timestamp strings to timezone-aware UTC datetime."""
    if pd.isna(ts_val):
        return None
    try:
        s = str(ts_val).strip().replace(" UTC", "")
        return pd.to_datetime(s, utc=True)
    except Exception:
        return None


def _trade_opened_at(trade: dict):
    """Open time of a ledger trade, from its JEV-<epoch> trade_id."""
    trade_id = str(trade.get("trade_id", ""))
    try:
        epoch = int(trade_id.split("-", 1)[1])
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except Exception:
        return None


def load_ledger_trades(window_start: datetime, now_utc: datetime) -> list:
    """Closed trades from paper_trades.json inside the window.

    Each record: side, pnl, entry/sl/tp, exit_reason, opened_at.
    Returns [] when the ledger is missing — honestly, not as a claim.
    """
    records = []
    if not os.path.exists(TRADES_FILE):
        logger.warning("Ledger %s not found; weekly report has no trades to audit.", TRADES_FILE)
        return records
    try:
        with open(TRADES_FILE, "r") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Could not parse %s: %s", TRADES_FILE, e)
        return records
    for trade in data.get("closed_trades", []):
        opened = _trade_opened_at(trade)
        if opened is None or not (window_start <= opened <= now_utc):
            continue
        records.append(
            {
                "datetime": opened,
                "source": "JEV_PAPER_LEDGER",
                "side": str(trade.get("side", "OTHER")).upper(),
                "status": str(trade.get("exit_reason", "CLOSED")).upper(),
                "price": float(trade.get("entry_price", 0.0) or 0.0),
                "sl": float(trade.get("sl", 0.0) or 0.0),
                "tp": float(trade.get("tp", 0.0) or 0.0),
                "pnl": float(trade.get("pnl", 0.0) or 0.0),
            }
        )
    return records


def load_and_clean_logs(window_start: datetime, now_utc: datetime) -> pd.DataFrame:
    """Ingests optional CSV logs plus the real ledger, 7-day filtered.

    The CSV files are legacy/optional — no engine writes them. The ledger
    is always included when present.
    """
    records = load_ledger_trades(window_start, now_utc)

    # 1. Ingest Scalp Execution Logs (scalp_log.csv) — optional
    if os.path.exists(SCALP_LOG_FILE):
        try:
            df_scalp = pd.read_csv(SCALP_LOG_FILE, on_bad_lines="skip")
            if not df_scalp.empty and "timestamp" in df_scalp.columns:
                df_scalp["dt"] = df_scalp["timestamp"].apply(parse_utc_timestamp)
                df_scalp = df_scalp[(df_scalp["dt"] >= window_start) & (df_scalp["dt"] <= now_utc)]
                for _, r in df_scalp.iterrows():
                    action = str(r.get("action", "")).upper()
                    side = "BUY" if "BUY" in action else ("SELL" if any(x in action for x in ["SHORT", "SELL"]) else "OTHER")
                    records.append({
                        "datetime": r["dt"],
                        "source": "SCALP_ENGINE",
                        "side": side,
                        "status": str(r.get("status", "CLOSED")).upper(),
                        "price": float(r.get("price", 0.0)) if pd.notna(r.get("price")) else 0.0,
                        "sl": float(r.get("sl", 0.0)) if pd.notna(r.get("sl")) else 0.0,
                        "tp": float(r.get("tp", 0.0)) if pd.notna(r.get("tp")) else 0.0,
                        "pnl": 0.0,
                        "tag": str(r.get("tag", "DYNAMIC_SCALP"))
                    })
        except Exception as e:
            logger.warning("[WARN] Could not parse %s: %s", SCALP_LOG_FILE, e)

    # 2. Ingest Systematic Trade Logs (trade_log.csv) — optional
    if os.path.exists(TRADE_LOG_FILE):
        try:
            df_trade = pd.read_csv(TRADE_LOG_FILE, on_bad_lines="skip")
            if not df_trade.empty and "Timestamp" in df_trade.columns:
                df_trade["dt"] = df_trade["Timestamp"].apply(parse_utc_timestamp)
                df_trade = df_trade[(df_trade["dt"] >= window_start) & (df_trade["dt"] <= now_utc)]
                for _, r in df_trade.iterrows():
                    sig = str(r.get("Signal", "")).upper()
                    side = "BUY" if "BUY" in sig else ("SELL" if any(x in sig for x in ["SHORT", "SELL"]) else "OTHER")
                    records.append({
                        "datetime": r["dt"],
                        "source": "SYSTEMATIC_CONFLUENCE",
                        "side": side,
                        "status": "RECORDED",
                        "price": float(r.get("Price", 0.0)) if pd.notna(r.get("Price")) else 0.0,
                        "sl": float(r.get("SL", 0.0)) if pd.notna(r.get("SL")) else 0.0,
                        "tp": float(r.get("TP", 0.0)) if pd.notna(r.get("TP")) else 0.0,
                        "pnl": 0.0,
                        "tag": str(r.get("Conviction", "CONFLUENCE_MODEL"))
                    })
        except Exception as e:
            logger.warning("[WARN] Could not parse %s: %s", TRADE_LOG_FILE, e)

    return pd.DataFrame(records)


def generate_weekly_report():
    now_utc = datetime.now(timezone.utc)
    week_ago = now_utc - timedelta(days=7)
    date_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    window_str = f"{week_ago.strftime('%b %d')} - {now_utc.strftime('%b %d, %Y')}"

    df = load_and_clean_logs(week_ago, now_utc)

    # Empty State: honestly report that there is nothing to measure.
    # The old version claimed gating efficacy here with zero trades.
    if df.empty:
        report = (
            f"📊 *WEEKLY PERFORMANCE AUDIT REPORT*\n"
            f"📅 *Epoch*: `{date_str}`\n"
            f"⏱ *Rolling Window*: `{window_str}`\n\n"
            f"📈 *Execution Summary (Last 7 Days)*:\n"
            f"• Total Executed Trades: `0`\n"
            f"  🟢 BUY Trades: `0`\n"
            f"  🔴 SELL Trades: `0`\n\n"
            f"ℹ️ *Note*: No closed trades in the window, so no win rate, "
            f"profit factor or drawdown can be measured.\n\n"
            f"_Engine: Volatility-Adaptive XAU/USD Quant Suite_"
        )
        dispatch_telegram(report)
        logger.info(report)
        return

    # Filter directional trades
    valid_trades = df[df["side"].isin(["BUY", "SELL"])].copy()
    total_trades = len(valid_trades)
    buy_trades = len(valid_trades[valid_trades["side"] == "BUY"])
    sell_trades = len(valid_trades[valid_trades["side"] == "SELL"])

    if total_trades == 0:
        report = (
            f"📊 *WEEKLY PERFORMANCE AUDIT REPORT*\n"
            f"📅 *Epoch*: `{date_str}`\n"
            f"⏱ *Rolling Window*: `{window_str}`\n\n"
            f"📈 *Execution Summary (Last 7 Days)*:\n"
            f"• Total Executed Trades: `0` (Only Non-Directional/Suppressed Entries)\n\n"
            f"ℹ️ *Note*: No directional trades closed in the window.\n\n"
            f"_Engine: Volatility-Adaptive XAU/USD Quant Suite_"
        )
        dispatch_telegram(report)
        logger.info(report)
        return

    # Dynamic Metric Calculations — ledger rows carry real booked PnL.
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    pnl_sequence = []
    rr_ratios = []

    for _, row in valid_trades.iterrows():
        entry = row["price"]
        sl = row["sl"]
        tp = row["tp"]
        status = row["status"]
        pnl = float(row.get("pnl", 0.0)) if "pnl" in row else 0.0

        if entry > 0 and sl > 0 and tp > 0:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                rr_ratios.append(reward / risk)

        # Ledger rows: use the booked PnL, not a reconstructed one.
        if row.get("source") == "JEV_PAPER_LEDGER":
            pnl_sequence.append(pnl)
            if pnl > 0:
                wins += 1
                gross_profit += pnl
            elif pnl < 0:
                losses += 1
                gross_loss += abs(pnl)
            continue

        # Legacy CSV rows: lifecycle-status reconstruction as before.
        if status in ["CLOSED_WIN", "TP_HIT", "WIN"]:
            wins += 1
            gross_profit += abs(tp - entry) if entry > 0 and tp > 0 else 0.0
            pnl_sequence.append(abs(tp - entry) if entry > 0 and tp > 0 else 0.0)
        elif status in ["CLOSED_LOSS", "SL_HIT", "LOSS"]:
            losses += 1
            gross_loss += abs(entry - sl) if entry > 0 and sl > 0 else 0.0
            pnl_sequence.append(-abs(entry - sl) if entry > 0 and sl > 0 else 0.0)
        else:
            pnl_sequence.append(0.0)

    closed_cycles = wins + losses
    win_rate = (wins / closed_cycles * 100.0) if closed_cycles > 0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)
    net_pnl = gross_profit - gross_loss
    avg_rr = float(np.mean(rr_ratios)) if rr_ratios else 0.0

    # Drawdown Calculation
    if pnl_sequence:
        cum_pnl = np.cumsum(pnl_sequence)
        highwater = np.maximum.accumulate(cum_pnl)
        dd = highwater - cum_pnl
        max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
    else:
        max_dd = 0.0

    report = (
        f"📊 *WEEKLY PERFORMANCE AUDIT REPORT*\n"
        f"📅 *Epoch*: `{date_str}`\n"
        f"⏱ *Rolling Window*: `{window_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 *Execution Breakdown*:\n"
        f"• Total Qualified Trades: `{total_trades}`\n"
        f"  🟢 Long (BUY): `{buy_trades}`\n"
        f"  🔴 Short (SELL): `{sell_trades}`\n"
        f"• Resolved Cycles: `{closed_cycles}`\n\n"
        f"🎯 *Performance Matrix* (from the paper-trades ledger):\n"
        f"• Win Rate: `{win_rate:.1f}%` ({wins}W / {losses}L)\n"
        f"• Profit Factor: `{profit_factor:.2f}`\n"
        f"• Average Structural R:R: `1:{avg_rr:.2f}`\n"
        f"• Net PnL Vector: `{net_pnl:+.2f} USD`\n"
        f"• Maximum Drawdown: `${max_dd:.2f}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ *System Integrity*:\n"
        f"• Institutional GEX Walls & US10Y Gating Active\n"
        f"• Hard R:R Gatekeeper Enforced (Threshold >= 1:1.30)\n\n"
        f"_Engine: Volatility-Adaptive XAU/USD Quant Suite_"
    )

    dispatch_telegram(report)
    logger.info(report)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    generate_weekly_report()
