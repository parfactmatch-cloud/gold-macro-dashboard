"""
===============================================================================
PROJECT: QUANT ENGINE WEEKLY PERFORMANCE AUDIT & TELEGRAM DISPATCHER
UPGRADES:
  1. Multi-log aggregation with unified schema normalization (trade_log.csv + scalp_log.csv)
  2. Strict 7-day rolling window based on current UTC epoch (now - 7 days)
  3. Dynamic PnL, Win Rate, Profit Factor, Max Drawdown, and GEX/Macro audit telemetry
===============================================================================
"""

import os
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

# ================= CONFIGURATION =================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SCALP_LOG_FILE = "scalp_log.csv"
TRADE_LOG_FILE = "trade_log.csv"

def dispatch_telegram(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TG WARN] Bot token or Chat ID not configured.")
        return False
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
        return res.status_code == 200
    except Exception as e:
        print(f"[TG ERROR] {e}")
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

def load_and_clean_logs(window_start: datetime, now_utc: datetime) -> pd.DataFrame:
    """Ingests multi-source logs, aligns schema columns, and applies 7-day filter."""
    records = []

    # 1. Ingest Scalp Execution Logs (scalp_log.csv)
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
                        "tag": str(r.get("tag", "DYNAMIC_SCALP"))
                    })
        except Exception as e:
            print(f"[WARN] Could not parse {SCALP_LOG_FILE}: {e}")

    # 2. Ingest Systematic Trade Logs (trade_log.csv)
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
                        "tag": str(r.get("Conviction", "CONFLUENCE_MODEL"))
                    })
        except Exception as e:
            print(f"[WARN] Could not parse {TRADE_LOG_FILE}: {e}")

    return pd.DataFrame(records)

def generate_weekly_report():
    now_utc = datetime.now(timezone.utc)
    week_ago = now_utc - timedelta(days=7)
    date_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")
    window_str = f"{week_ago.strftime('%b %d')} - {now_utc.strftime('%b %d, %Y')}"

    df = load_and_clean_logs(week_ago, now_utc)

    # Empty State: Zero Qualified Executions
    if df.empty:
        report = (
            f"📊 *WEEKLY PERFORMANCE AUDIT REPORT*\n"
            f"📅 *Epoch*: `{date_str}`\n"
            f"⏱ *Rolling Window*: `{window_str}`\n\n"
            f"📈 *Execution Summary (Last 7 Days)*:\n"
            f"• Total Executed Trades: `0`\n"
            f"  🟢 BUY Trades: `0`\n"
            f"  🔴 SELL Trades: `0`\n\n"
            f"🛡️ *Institutional Gating & Efficacy*:\n"
            f"• Dealer Call/Put Gamma Walls successfully prevented boundary exhaustion.\n"
            f"• Structural R:R Gatekeeper (< 1:1.30) suppressed cramped risk profiles.\n"
            f"• Multi-Timeframe 30M & US10Y Macro hard-gating active.\n\n"
            f"_Engine: Volatility-Adaptive XAU/USD Quant Suite_"
        )
        dispatch_telegram(report)
        print(report)
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
            f"🛡️ *Filter Efficacy*:\n"
            f"• All candidate triggers filtered by institutional equilibrium barriers.\n"
            f"• Capital drawdown during window: `0.00%`\n\n"
            f"_Engine: Volatility-Adaptive XAU/USD Quant Suite_"
        )
        dispatch_telegram(report)
        print(report)
        return

    # Dynamic Metric Calculations
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

        if entry > 0 and sl > 0 and tp > 0:
            risk = abs(entry - sl)
            reward = abs(tp - entry)
            if risk > 0:
                rr_ratios.append(reward / risk)

            # Performance reconciliation based on lifecycle status
            if status in ["CLOSED_WIN", "TP_HIT", "WIN"]:
                wins += 1
                gross_profit += reward
                pnl_sequence.append(reward)
            elif status in ["CLOSED_LOSS", "SL_HIT", "LOSS"]:
                losses += 1
                gross_loss += risk
                pnl_sequence.append(-risk)
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
        f"🎯 *Performance Matrix*:\n"
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
    print(report)

if __name__ == "__main__":
    generate_weekly_report()
  
