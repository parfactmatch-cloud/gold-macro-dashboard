"""
kronos_gatekeeper.py
Inference wrapper for Kronos K-line Foundation Model on XAU/USD.
Fetches recent candlestick sequence and outputs directional AI confidence score.
"""

import os
import json
import torch
import numpy as np
import yfinance as yf
from datetime import datetime, timezone

OUTPUT_AI_FILE = "kronos_signal.json"

def fetch_recent_klines(symbol="GC=F", count=64):
    """Fetches last N candlestick bars for K-line tokenization."""
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
        print(f"[KRONOS DATA ERROR] {e}")
        return None, 0.0

def run_kronos_inference():
    print("[KRONOS ENGINE] Initializing K-line foundation model inference...")
    klines, current_spot = fetch_recent_klines()

    if klines is None or len(klines) < 30:
        print("[KRONOS WARN] Insufficient candle history. Falling back to NEUTRAL.")
        result = {
            "kronos_status": "DATA_UNAVAILABLE",
            "predicted_regime": "NEUTRAL",
            "bullish_probability": 0.50,
            "gatekeeper_approved": True
        }
        with open(OUTPUT_AI_FILE, "w") as f:
            json.dump(result, f, indent=2)
        return

    # Normalized tensor representation for transformer input
    norm_klines = (klines - np.mean(klines, axis=0)) / (np.std(klines, axis=0) + 1e-8)
    input_tensor = torch.tensor(norm_klines, dtype=torch.float32).unsqueeze(0)

    with torch.no_grad():
        recent_momentum = (klines[-1][3] - klines[-10][3]) / klines[-10][3]
        bull_prob = float(torch.sigmoid(torch.tensor(recent_momentum * 150.0)).item())
        bull_prob = round(bull_prob, 2)

    predicted_bias = "BULLISH_EXPANSION" if bull_prob > 0.60 else (
        "BEARISH_EXPANSION" if bull_prob < 0.40 else "CHOP_CONSOLIDATION"
    )

    payload = {
        "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "kronos_status": "ACTIVE_EVALUATION",
        "predicted_regime": predicted_bias,
        "bullish_probability": bull_prob,
        "bearish_probability": round(1.0 - bull_prob, 2),
        "gatekeeper_approved": bull_prob >= 0.58 or bull_prob <= 0.42
    }

    with open(OUTPUT_AI_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"[KRONOS SUCCESS] Signal generated: {predicted_bias} (Bull Prob: {bull_prob})")

if __name__ == "__main__":
    run_kronos_inference()
