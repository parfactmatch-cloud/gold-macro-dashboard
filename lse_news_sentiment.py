"""
lse_news_sentiment.py
Institutional COT Positioning, Economic Shield Gate & Event Sentiment Analyzer.
Integrates with London Strategic Edge Databank API.

Endpoints utilized:
- /ref/cot (Commitments of Traders for GC Gold Futures)
- /ref/economic_calendar (Macro Shock Telemetry: CPI, NFP, FOMC, Rate Decisions)
"""

import os
import json
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta

LSE_API_KEY = os.getenv("LSE_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN", "") 
    or os.getenv("BOT_TOKEN", "")
).strip()
TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID", "") 
    or os.getenv("CHAT_ID", "")
).strip()

BASE_URL = "https://api.londonstrategicedge.com/vault"
NEWS_SENTIMENT_LOG = "news_sentiment_history.csv"
COT_CACHE_FILE = "cot_positioning.json"

HIGH_IMPACT_KEYWORDS = [
    "cpi", "consumer price index", 
    "non-farm", "nonfarm", "unemployment rate", 
    "fomc", "fed interest rate", "federal funds"
]

def dispatch_telegram(html_card: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        res = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": html_card, "parse_mode": "HTML"}, timeout=8)
        return res.status_code == 200
    except Exception as e:
        print(f"[TG DISPATCH ERROR] {e}")
        return False

# ================= 1. COT DATA POSITIONING ENGINE =================
def fetch_and_analyze_cot(symbol="GC") -> dict:
    """
    Fetches CFTC Commitment of Traders (COT) report for Gold Futures (GC).
    Measures Net Speculator Flow (Hedge Funds) vs Commercial Hedging.
    """
    if not LSE_API_KEY:
        return {"bias": "NEUTRAL", "net_spec_contracts": 0, "status": "KEY_MISSING"}

    headers = {"x-api-key": LSE_API_KEY}
    url = f"{BASE_URL}/ref/cot"
    params = {"symbol": symbol, "order": "desc", "limit": 2}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=12)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                latest = data[0]
                prev = data[1] if len(data) > 1 else latest
                
                # Extract Speculative & Commercial components
                non_comm_long = float(latest.get("non_commercial_long", 0) or latest.get("managed_money_long", 0))
                non_comm_short = float(latest.get("non_commercial_short", 0) or latest.get("managed_money_short", 0))
                net_spec = non_comm_long - non_comm_short

                prev_long = float(prev.get("non_commercial_long", 0) or prev.get("managed_money_long", 0))
                prev_short = float(prev.get("non_commercial_short", 0) or prev.get("managed_money_short", 0))
                prev_net = prev_long - prev_short

                delta_contracts = net_spec - prev_net
                
                if net_spec > 100000 and delta_contracts > 0:
                    cot_bias = "INSTITUTIONAL_ACCUMULATION_BULLISH"
                elif net_spec < 50000 or delta_contracts < -10000:
                    cot_bias = "INSTITUTIONAL_LIQUIDATION_BEARISH"
                else:
                    cot_bias = "MACRO_POSITIONING_NEUTRAL"

                payload = {
                    "symbol": symbol,
                    "date": latest.get("date", "N/A"),
                    "net_spec_contracts": int(net_spec),
                    "delta_weekly": int(delta_contracts),
                    "bias": cot_bias,
                    "status": "HEALTHY"
                }

                with open(COT_CACHE_FILE, "w") as f:
                    json.dump(payload, f, indent=2)

                print(f"[COT SUCCESS] Net Speculators: {net_spec:+,} contracts (Δ {delta_contracts:+,}) -> {cot_bias}")
                return payload
    except Exception as e:
        print(f"[COT EXCEPTION] {e}")

    return {"bias": "NEUTRAL", "net_spec_contracts": 0, "status": "ERROR"}

# ================= 2. ECONOMIC CALENDAR & NEWS SHIELD =================
def evaluate_news_shield_and_sentiment() -> dict:
    """
    Sweeps the US economic calendar for high-impact shock events (CPI, NFP, FOMC).
    Applies an auto-lock circuit breaker: 15 min prior & 15 min post release.
    """
    if not LSE_API_KEY:
        return {"shield_active": False, "reason": "NO_KEY", "sentiment": "NEUTRAL"}

    headers = {"x-api-key": LSE_API_KEY}
    url = f"{BASE_URL}/ref/economic_calendar"
    
    # 24-hour search window
    now_utc = datetime.now(timezone.utc)
    start_iso = (now_utc - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    end_iso = (now_utc + timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")

    params = {
        "region": "US",
        "start": start_iso,
        "end": end_iso,
        "order": "desc",
        "limit": 50
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=12)
        if res.status_code == 200:
            events = res.json()
            if not isinstance(events, list):
                return {"shield_active": False, "sentiment": "NEUTRAL"}

            shield_locked = False
            lock_reason = "NONE"
            sentiment_direction = "NEUTRAL"

            for ev in events:
                name = str(ev.get("event", "")).lower()
                is_major = any(k in name for k in HIGH_IMPACT_KEYWORDS)
                if not is_major:
                    continue

                event_time_str = ev.get("datetime") or ev.get("date")
                if not event_time_str:
                    continue

                try:
                    # Clean ISO parser
                    ev_dt = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
                except Exception:
                    continue

                delta_minutes = (ev_dt - now_utc).total_seconds() / 60.0

                # 15-minute Pre & Post Shield Rule
                if -15.0 <= delta_minutes <= 15.0:
                    shield_locked = True
                    lock_reason = f"HIGH_IMPACT_NEWS: {ev.get('event')} at {event_time_str}"

                # Calculate Released Surprise Delta for Algorithm Memory
                is_released = ev.get("released", 0) == 1 or ev.get("actual") is not None
                if is_released and -180.0 <= delta_minutes <= 0.0:
                    actual = float(ev.get("actual", 0.0) or 0.0)
                    forecast = float(ev.get("forecast", actual) or actual)
                    surprise = actual - forecast

                    if "cpi" in name or "inflation" in name:
                        # Higher CPI = Hawkish Fed = Bearish for Gold
                        sentiment_direction = "BEARISH_SHOCK" if surprise > 0.1 else ("BULLISH_SHOCK" if surprise < -0.1 else "INLINE")
                    elif "non-farm" in name or "payroll" in name:
                        # Higher NFP = Strong Economy = Bearish for Gold
                        sentiment_direction = "BEARISH_SHOCK" if surprise > 25.0 else ("BULLISH_SHOCK" if surprise < -25.0 else "INLINE")
                    elif "fomc" in name or "interest rate" in name:
                        sentiment_direction = "BEARISH_SHOCK" if surprise > 0.0 else ("BULLISH_SHOCK" if surprise < 0.0 else "INLINE")

                    # Log Event Surprise into CSV Machine Memory
                    log_entry = pd.DataFrame([{
                        "timestamp": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        "event": ev.get("event"),
                        "actual": actual,
                        "forecast": forecast,
                        "surprise": surprise,
                        "sentiment_verdict": sentiment_direction
                    }])
                    log_entry.to_csv(NEWS_SENTIMENT_LOG, mode="a", header=not os.path.exists(NEWS_SENTIMENT_LOG), index=False)

                    # Trigger Telegram Shock Alert
                    send_news_alert_card(ev.get("event"), actual, forecast, surprise, sentiment_direction)
                    break

            return {
                "shield_active": shield_locked,
                "lock_reason": lock_reason,
                "sentiment": sentiment_direction
            }
    except Exception as e:
        print(f"[SHIELD EXCEPTION] {e}")

    return {"shield_active": False, "sentiment": "NEUTRAL"}

# ================= 3. TELEGRAM NEWS SHOCK CARD =================
def send_news_alert_card(event_name: str, actual: float, forecast: float, surprise: float, sentiment: str):
    icon = "⚡🟢 <b>MACRO NEWS BULLISH SHOCK (GOLD UP)</b>" if sentiment == "BULLISH_SHOCK" else (
           "⚡🔴 <b>MACRO NEWS BEARISH SHOCK (GOLD DOWN)</b>" if sentiment == "BEARISH_SHOCK" else "⚡⚪ <b>MACRO NEWS IN-LINE RELEASE</b>")
    
    impact_tag = "🟩 Bullish Tailwind (Yields Expected to Compress)" if sentiment == "BULLISH_SHOCK" else (
                 "🟥 Bearish Pressure (Hawkish Fed Expectation)" if sentiment == "BEARISH_SHOCK" else "🟨 In-Line Baseline")

    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    card = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛 <b>Event</b>: <code>{event_name}</code>\n"
        f"📊 <b>Release Data</b>: <code>Actual: {actual} | Forecast: {forecast}</code>\n"
        f"📐 <b>Surprise Gap</b>: <code>{surprise:+.2f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🧭 <b>Market Bias</b>: {impact_tag}\n"
        f"🛡️ <b>Execution Gate</b>: Logged to Engine Memory\n"
        f"⏰ <b>Epoch</b>: <code>{now_utc}</code>"
    )
    dispatch_telegram(card)

if __name__ == "__main__":
    cot_res = fetch_and_analyze_cot()
    shield_res = evaluate_news_shield_and_sentiment()
    print(f"[STATUS] Shield Active: {shield_res.get('shield_active')} | Verdict: {shield_res.get('sentiment')}")
              
