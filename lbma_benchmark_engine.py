"""
lbma_benchmark_engine.py
Institutional LBMA Benchmark Auction & Settlement Timing Engine (IST Native):
- Native Indian Standard Time (Asia/Kolkata) Timing & Reporting
- Tracks London 10:30 AM (AM Fix) & 3:00 PM (PM Fix) Electronic IBA Auctions
- Detects Pre-Fix Shading / Ramp-up Windows (Front-Running Protection)
- Flags Post-Fix Elastic Snap-Back / Mean-Reversion Windows
- Acts as an Execution Gatekeeper for jev_paper_engine.py
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LBMA_STATE_FILE = os.path.join(SCRIPT_DIR, "lbma_status.json")


def get_current_times():
    """
    Returns synced IST and London datetime objects.
    London time is retained internally to precisely handle GMT/BST daylight transitions,
    while all telemetry and schedules are expressed natively in IST.
    """
    now_utc = datetime.now(timezone.utc)
    ist_dt = now_utc.astimezone(ZoneInfo("Asia/Kolkata"))
    ldn_dt = now_utc.astimezone(ZoneInfo("Europe/London"))
    return ist_dt, ldn_dt


def inspect_lbma_auction_window() -> dict:
    """
    Evaluates current IST time against LBMA Gold Price auction windows.
    IST Corresponding Timings (during British Summer Time / London Daylight):
      - AM Fix:
          * Pre-Fix Ramp:  3:05 PM - 3:29 PM IST
          * Live Auction:  3:30 PM - 3:34 PM IST
          * Post-Fix Snap: 3:35 PM - 3:55 PM IST
      - PM Fix:
          * Pre-Fix Ramp:  7:35 PM - 7:59 PM IST
          * Live Auction:  8:00 PM - 8:04 PM IST
          * Post-Fix Snap: 8:05 PM - 8:25 PM IST
    """
    ist_dt, ldn_dt = get_current_times()
    weekday = ist_dt.weekday()  # 0=Monday, 4=Friday, 5=Saturday, 6=Sunday

    ist_time_str = ist_dt.strftime("%I:%M %p IST")
    ldn_mins = ldn_dt.hour * 60 + ldn_dt.minute

    # Weekend Protection (No LBMA auctions on Saturday/Sunday)
    if weekday >= 5:
        return {
            "is_weekend": True,
            "phase": "MARKET_CLOSED_WEEKEND",
            "execution_policy": "NORMAL",
            "rr_multiplier": 1.0,
            "risk_weight": 1.0,
            "ist_time": ist_time_str,
            "notes": "LBMA benchmark auctions do not operate on weekends."
        }

    phase = "CONTINUOUS_TRADING"
    execution_policy = "NORMAL"
    rr_multiplier = 1.0
    risk_weight = 1.0
    notes = "Standard auction flow. No active benchmark auction distortions."

    # 1. Pre-Fix Ramp / Inventory Shading Window
    # AM Fix Window (Around 3:05 PM - 3:29 PM IST)
    # PM Fix Window (Around 7:35 PM - 7:59 PM IST)
    if (605 <= ldn_mins < 630) or (875 <= ldn_mins < 900):
        fix_name = "AM_FIX" if ldn_mins < 630 else "PM_FIX"
        phase = f"PRE_{fix_name}_RAMP_SHADING"
        execution_policy = "BLOCK_MEAN_REVERSION_MOMENTUM_ONLY"
        risk_weight = 0.70  # Reduce risk size due to institutional front-running/shading
        rr_multiplier = 1.0
        notes = f"Pre-{fix_name} inventory shading active in London. Counter-trend fades blocked."

    # 2. Live Electronic IBA Auction Balancing Rounds (Peak Volatility & Spread Expansion)
    # AM Fix Peak (Around 3:30 PM - 3:34 PM IST)
    # PM Fix Peak (Around 8:00 PM - 8:04 PM IST)
    elif (630 <= ldn_mins <= 634) or (900 <= ldn_mins <= 904):
        fix_name = "AM_FIX" if ldn_mins <= 634 else "PM_FIX"
        phase = f"LIVE_{fix_name}_AUCTION_MATCHING"
        execution_policy = "TOTAL_EXECUTION_BLACKOUT"
        risk_weight = 0.0
        rr_multiplier = 1.0
        notes = f"Live IBA matching round running for {fix_name}. Execution strictly paused."

    # 3. Post-Fix Elastic Snap-Back (Artificial Imbalance Unwinding / Liquidity Vacuum)
    # AM Fix Post-Snap (Around 3:35 PM - 3:55 PM IST)
    # PM Fix Post-Snap (Around 8:05 PM - 8:25 PM IST)
    elif (635 <= ldn_mins <= 655) or (905 <= ldn_mins <= 925):
        fix_name = "AM_FIX" if ldn_mins <= 655 else "PM_FIX"
        phase = f"POST_{fix_name}_ELASTIC_SNAPBACK"
        execution_policy = "FAVOR_REVERSION_EXPANDED_TARGETS"
        risk_weight = 1.25  # High-confidence post-auction snapback
        rr_multiplier = 1.65 # Expanded R:R target (2.5R+)
        notes = f"{fix_name} auction completed. Imbalances snapping back toward session fair value."

    payload = {
        "is_weekend": False,
        "phase": phase,
        "execution_policy": execution_policy,
        "rr_multiplier": rr_multiplier,
        "risk_weight": risk_weight,
        "ist_time": ist_time_str,
        "notes": notes
    }

    try:
        with open(LBMA_STATE_FILE, "w") as f:
            json.dump(payload, f, indent=2)
    except Exception:
        pass

    return payload


if __name__ == "__main__":
    status = inspect_lbma_auction_window()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("🏛️ LBMA BENCHMARK AUCTION RADAR (IST)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"⏰ Current IST Time  : {status['ist_time']}")
    print(f"📍 Market Phase      : {status['phase']}")
    print(f"🛡️ Execution Policy  : {status['execution_policy']}")
    print(f"⚖️ Risk Multiplier   : {status['risk_weight']}x")
    print(f"🎯 Target Expander   : {status['rr_multiplier']}x")
    print(f"📝 Telemetry Notes   : {status['notes']}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
