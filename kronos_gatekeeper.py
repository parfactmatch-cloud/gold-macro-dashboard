"""
kronos_gatekeeper.py
Momentum regime check for XAU/USD (formerly presented as "Kronos K-line
Foundation Model" / "Neural AI").

Honest description: this is NOT a neural network and loads no model
weights. It fetches the last 64 five-minute candles, computes the
10-bar close momentum, and maps it through a sigmoid to a bullish
probability. The torch dependency was removed for exactly that reason —
a sigmoid does not need a deep-learning framework.

Regime: bull_prob > 0.60 BULLISH_EXPANSION, < 0.40 BEARISH_EXPANSION,
otherwise CHOP_CONSOLIDATION. The probability saturates fast
(sigmoid(momentum * 150)): a 0.5% 10-bar move already gives ~0.68, so
treat it as a coarse directional filter, not a calibrated probability.
"""

import json
import logging
import math
import os

import numpy as np
import yfinance as yf
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_AI_FILE = os.path.join(SCRIPT_DIR, "kronos_signal.json")

# Label carried in the output so downstream consumers stop calling this
# a neural model.
MODEL_LABEL = "momentum_sigmoid_10bar"


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def fetch_recent_klines(symbol="GC=F", count=64):
    """Fetches last N candlestick bars for the momentum calculation."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="2d", interval="5m")
        if len(df) < count:
            df = ticker.history(period="5d", interval="15m")
        df = df.tail(count)

        # Open, High, Low, Close, Volume
        klines = df[["Open", "High", "Low", "Close", "Volume"]].values
        return klines, float(df["Close"].iloc[-1])
    except Exception as e:
        logger.warning("[KRONOS DATA ERROR] %s", e)
        return None, 0.0


def run_kronos_inference():
    logger.info("[KRONOS] Running momentum regime check (10-bar sigmoid, not a neural model)...")
    klines, current_spot = fetch_recent_klines()

    if klines is None or len(klines) < 30:
        logger.warning("[KRONOS WARN] Insufficient candle history. Falling back to NEUTRAL.")
        result = {
            "kronos_status": "DATA_UNAVAILABLE",
            "model": MODEL_LABEL,
            "predicted_regime": "NEUTRAL",
            "bullish_probability": 0.50,
            "gatekeeper_approved": True,
        }
        with open(OUTPUT_AI_FILE, "w") as f:
            json.dump(result, f, indent=2)
        return

    # 10-bar close momentum -> sigmoid. No tensor, no model weights.
    recent_momentum = (klines[-1][3] - klines[-10][3]) / klines[-10][3]
    bull_prob = round(_sigmoid(float(recent_momentum) * 150.0), 2)

    predicted_bias = "BULLISH_EXPANSION" if bull_prob > 0.60 else (
        "BEARISH_EXPANSION" if bull_prob < 0.40 else "CHOP_CONSOLIDATION"
    )

    payload = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "kronos_status": "ACTIVE_EVALUATION",
        "model": MODEL_LABEL,
        "predicted_regime": predicted_bias,
        "bullish_probability": bull_prob,
        "bearish_probability": round(1.0 - bull_prob, 2),
        "gatekeeper_approved": bull_prob >= 0.58 or bull_prob <= 0.42,
    }

    with open(OUTPUT_AI_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    logger.info("[KRONOS SUCCESS] Signal generated: %s (Bull Prob: %s)", predicted_bias, bull_prob)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    run_kronos_inference()
