"""
profile_tpo_guard_engine.py
Institutional Market Profile & TPO Guardrail Engine
Based on Garvit Garg (Trade Nidar) Lecture 2 & Lecture 3:
- Volume Profile Shape Classification (P-Shape, b-Shape, D-Shape)
- Single Timeframing Momentum Circuit Breaker (Prevents fading active 1-way directional auctions)
- Multi-Timeframe Structural Guardrails
"""

import math
import pandas as pd
import yfinance as yf


def inspect_profile_tpo_guards(ticker_symbol: str = "GC=F") -> dict:
    """
    Evaluates Profile Shapes and Single Timeframing conditions on 5M/15M intraday data.
    Returns operational permission flags:
      - block_short_fade: True if P-shape or active Bullish Single Timeframing
      - block_long_bounce: True if b-shape or active Bearish Single Timeframing
    """
    fallback = {
        "status": "FALLBACK_NEUTRAL",
        "profile_shape": "D_SHAPE_BALANCED",
        "single_timeframing": "NONE",
        "block_short_fade": False,
        "block_long_bounce": False,
        "stf_count": 0,
        "poc_ratio": 0.50
    }

    try:
        t = yf.Ticker(ticker_symbol)
        df = t.history(period="2d", interval="5m")
        if df.empty or len(df) < 25:
            return fallback

        # 1. Evaluate Single Timeframing (STF) on the last 5 bars
        recent = df.tail(6).copy()
        highs = recent["High"].tolist()
        lows = recent["Low"].tolist()

        bullish_stf_bars = 0
        bearish_stf_bars = 0

        # Check consecutive higher highs & higher lows
        for i in range(1, len(highs)):
            if highs[i] > highs[i - 1] and lows[i] >= lows[i - 1]:
                bullish_stf_bars += 1
            else:
                bullish_stf_bars = 0

            if lows[i] < lows[i - 1] and highs[i] <= highs[i - 1]:
                bearish_stf_bars += 1
            else:
                bearish_stf_bars = 0

        single_timeframing = "NONE"
        block_short_fade = False
        block_long_bounce = False

        if bullish_stf_bars >= 3:
            single_timeframing = f"BULLISH_STF_ACTIVE ({bullish_stf_bars}_BARS)"
            block_short_fade = True

        if bearish_stf_bars >= 3:
            single_timeframing = f"BEARISH_STF_ACTIVE ({bearish_stf_bars}_BARS)"
            block_long_bounce = True

        # 2. Evaluate Session Profile Shape (P vs b vs D)
        # Using today's intraday bars to compute approximate Volume POC location
        session_df = df.tail(48).copy()  # ~4 hours of session structure
        session_high = session_df["High"].max()
        session_low = session_df["Low"].min()
        session_range = session_high - session_low

        profile_shape = "D_SHAPE_BALANCED"
        poc_ratio = 0.50

        if session_range > 2.0:
            # Approximate Volume-weighted POC via bar midpoints
            session_df["Typical_Price"] = (session_df["High"] + session_df["Low"] + session_df["Close"]) / 3.0
            
            # Bucket into 15 price bins
            session_df["Price_Bin"] = pd.cut(session_df["Typical_Price"], bins=15)
            bin_vols = session_df.groupby("Price_Bin", observed=False)["Volume"].sum()
            
            if not bin_vols.empty:
                poc_bin = bin_vols.idxmax()
                poc_mid = poc_bin.mid
                poc_ratio = round((poc_mid - session_low) / session_range, 2)

                # Lecture 2/3 Classification Rules:
                # P-Shape: POC in upper 30% of range (ratio >= 0.70) with thin bottom tail
                if poc_ratio >= 0.70:
                    profile_shape = "P_SHAPE_SHORT_COVERING"
                    block_short_fade = True  # P-Shape is bullish; do not fade VAH

                # b-Shape: POC in lower 30% of range (ratio <= 0.30) with thin top tail
                elif poc_ratio <= 0.30:
                    profile_shape = "B_SHAPE_LONG_LIQUIDATION"
                    block_long_bounce = True  # b-Shape is bearish; do not catch falling knife at VAL

                else:
                    profile_shape = "D_SHAPE_BALANCED"

        return {
            "status": "VALID",
            "profile_shape": profile_shape,
            "single_timeframing": single_timeframing,
            "block_short_fade": block_short_fade,
            "block_long_bounce": block_long_bounce,
            "stf_count": max(bullish_stf_bars, bearish_stf_bars),
            "poc_ratio": poc_ratio
        }

    except Exception:
        return fallback


if __name__ == "__main__":
    res = inspect_profile_tpo_guards("GC=F")
    print("=== PROFILE & TPO GUARD TELEMETRY ===")
    print(f"Profile Shape       : {res['profile_shape']} (POC Ratio: {res['poc_ratio']})")
    print(f"Single Timeframing  : {res['single_timeframing']}")
    print(f"Block Short Fade    : {res['block_short_fade']}")
    print(f"Block Long Bounce   : {res['block_long_bounce']}")
