"""
news_gatekeeper.py
Institutional High-Impact Economic Calendar Gatekeeper for Gold (XAU/USD)
- Monitors USD high-impact events (NFP, CPI, FOMC, PPI, GDP, Retail Sales)
- Imposes 30-min Pre-Event & 15-min Post-Event Trade Blackout
- Outputs news_status.json for Jev execution engine
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta

OUTPUT_NEWS_FILE = "news_status.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

HIGH_IMPACT_KEYWORDS = [
    "CPI", "NON-FARM", "NFP", "FOMC", "FEDERAL FUNDS RATE",
    "PPI", "GDP", "UNEMPLOYMENT RATE", "RETAIL SALES", "POWELL"
]


def send_tg_msg(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=8)
    except Exception:
        pass


def check_economic_calendar() -> dict:
    now_utc = datetime.now(timezone.utc)
    pre_window = timedelta(minutes=30)
    post_window = timedelta(minutes=15)

    blackout_active = False
    event_name = None
    event_time_str = None
    minutes_diff = 0

    # Public Economic Calendar Endpoint (ForexFactory format)
    url = "https://nfs.faireconomy.media/ff_calendar_thisWeek.json"
    try:
        res = requests.get(url, timeout=8)
        if res.status_code == 200:
            events = res.json()
            for ev in events:
                country = ev.get("country", "").upper()
                impact = ev.get("impact", "").title()
                title = ev.get("title", "").upper()
                date_str = ev.get("date", "")

                # Only target USD High Impact events or specific keywords
                if country == "USD" and (impact == "High" or any(kw in title for kw in HIGH_IMPACT_KEYWORDS)):
                    try:
                        # Parsing ISO timestamp
                        ev_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                        diff_sec = (ev_dt - now_utc).total_seconds()

                        # In blackout window: 30 mins before OR 15 mins after
                        if -900 <= diff_sec <= 1800:
                            blackout_active = True
                            event_name = ev.get("title")
                            event_time_str = ev_dt.strftime("%H:%M UTC")
                            minutes_diff = int(diff_sec // 60)
                            break
                    except Exception:
                        continue
    except Exception as e:
        print(f"[NEWS GATEKEEPER WARN] Calendar fetch skipped/failed: {e}")

    result = {
        "timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "blackout_active": blackout_active,
        "event_name": event_name,
        "event_time_utc": event_time_str,
        "minutes_to_event": minutes_diff
    }

    with open(OUTPUT_NEWS_FILE, "w") as f:
        json.dump(result, f, indent=2)

    if blackout_active:
        print(f"[NEWS GATEKEEPER] BLACKOUT ACTIVE: {event_name} at {event_time_str}")
        alert = (
            f"⚠️ <b>MACRO HIGH-IMPACT NEWS BLACKOUT ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🏛 <b>Event</b>: <code>{event_name}</code>\n"
            f"⏰ <b>Scheduled</b>: <code>{event_time_str}</code> (in {minutes_diff}m)\n"
            f"🛡️ <b>Shield Protocol</b>: Fresh trades strictly blocked. Liquidity vacuum expected."
        )
        send_tg_msg(alert)
    else:
        print("[NEWS GATEKEEPER] Macro Calendar Clear. No USD high-impact event in window.")

    return result


if __name__ == "__main__":
    check_economic_calendar()     
