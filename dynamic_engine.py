import os
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

SCALP_LOG_FILE = "scalp_log.csv"

def evaluate_dynamic_scalp(df_5m, spot_price, us10y_vector):
    """
    100% Dynamic Quant Engine:
    - Zero hardcoded prices.
    - Adapts to any price range via relative ATR and Rolling Volatility.
    - Yield-driven directional gating.
    """
    if len(df_5m) < 30:
        return

    # 1. डेटा स्ट्रक्चर और रनटाइम बार सिंक्रोनाइज़ेशन
    df = df_5m.copy()
    df.loc[df.index[-1], "close"] = spot_price

    # 2. डायनेमिक इंडिकेटर मैट्रिक्स
    ema_fast = df["close"].ewm(span=7, adjust=False).mean()
    ema_slow = df["close"].ewm(span=21, adjust=False).mean()
    
    curr_ema_fast = float(ema_fast.iloc[-1])
    curr_ema_slow = float(ema_slow.iloc[-1])
    
    # 3. डायनेमिक वोलैटिलिटी (14-पीरियड ATR)
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1])
    atr = atr if not np.isnan(atr) and atr > 0 else 2.50

    # 4. रिलेटिव कैंडल डायनेमिक्स (कोई फिक्स डॉलर नहीं, सिर्फ रेश्यो)
    curr_o = float(df["open"].iloc[-1])
    curr_h = float(df["high"].iloc[-1])
    curr_l = float(df["low"].iloc[-1])
    
    body = abs(spot_price - curr_o)
    upper_wick = curr_h - max(spot_price, curr_o)
    lower_wick = min(spot_price, curr_o) - curr_l

    # संतुलन स्तर (Equilibrium) से विचलन दूरी (ATR मल्टीपल में)
    deviation = (spot_price - curr_ema_slow) / atr

    # पिछले 10 कैंडल्स का लिक्विडिटी बेस और रूफ
    rolling_low = float(df["low"].iloc[-11:-1].min())
    rolling_high = float(df["high"].iloc[-11:-1].max())

    # =========================================================================
    # सेटअप A: वोलैटिलिटी एग्जॉशन शॉर्ट (हर रेंज में काम करेगा)
    # =========================================================================
    # शर्त: प्राइस 21-EMA से 1.5x ATR से ज्यादा खिंच चुका है + रिजेक्शन विक + यील्ड बुलिश है
    if deviation > 1.5 and upper_wick > (body * 1.2) and us10y_vector == "BEARISH_PRESSURE":
        sl = round(curr_h + (atr * 0.25), 2)  # डायनेमिक SL स्पाइक के थोड़ा ऊपर
        tp = round(curr_ema_slow, 2)         # हमेशा 21-EMA पर रिवर्जन टारगेट
        dispatch_execution("SHORT", spot_price, sl, tp, "VOLATILITY_EXHAUSTION_FADE")
        return

    # =========================================================================
    # सेटअप B: संस्थागत लिक्विडिटी हंट लॉन्ग (हर रेंज में काम करेगा)
    # =========================================================================
    # शर्त: पिछले बेस के नीचे डुबकी मारी लेकिन तुरंत वापस ऊपर क्लोज हुआ + यील्ड बाधक नहीं है
    liquidity_trapped = (curr_l < rolling_low) and (spot_price > rolling_low) and (lower_wick > body * 1.2)
    
    if liquidity_trapped and us10y_vector != "BEARISH_PRESSURE":
        sl = round(curr_l - (atr * 0.25), 2)  # विक के नीचे डायनेमिक SL
        risk = spot_price - sl
        tp = round(spot_price + (risk * 2.0), 2) # असिमेट्रिक 1:2 R:R
        dispatch_execution("BUY", spot_price, sl, tp, "LIQUIDITY_ABSORPTION_SWEEP")
        return

    # =========================================================================
    # सेटअप C: ट्रेंड कंटिन्युएशन पुलबैक (एंटी-चेज़िंग गार्ड)
    # =========================================================================
    # शर्त: संतुलन के करीब (0.8x ATR से कम दूरी) + ट्रेंड की दिशा में रीबाउंड
    if abs(deviation) < 0.8:
        # बुलिश रीटेस्ट
        if spot_price > curr_ema_slow and curr_l <= curr_ema_fast and spot_price > curr_o and us10y_vector != "BEARISH_PRESSURE":
            sl = round(curr_ema_slow - (atr * 0.50), 2)
            risk = spot_price - sl
            tp = round(spot_price + (risk * 2.0), 2)
            dispatch_execution("BUY", spot_price, sl, tp, "TREND_EQUILIBRIUM_PULLBACK")


def dispatch_execution(side, spot, sl, tp, regime_tag):
    if os.path.exists(SCALP_LOG_FILE):
        try:
            df_log = pd.read_csv(SCALP_LOG_FILE)
            if not df_log.empty and (df_log["status"] == "OPEN").any():
                return
        except Exception:
            pass

    risk = abs(spot - sl)
    reward = abs(tp - spot)
    rr = round(reward / risk, 2) if risk > 0 else 0
    now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    icon = "⚡🟢 *DYNAMIC LONG*" if side == "BUY" else "⚡🔴 *DYNAMIC SHORT*"
    card = (
        f"{icon}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏷 *Structure Model*: `{regime_tag}`\n"
        f"💵 *Dynamic Spot*: `${spot:.2f}`\n"
        f"📐 *Risk Profile*: `SL Risk: ${risk:.2f} | Target Reward: ${reward:.2f} (1:{rr})`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛑 *Dynamic SL*: `${sl:.2f}`\n"
        f"🎯 *Dynamic Target*: `${tp:.2f}`\n"
        f"⏰ *Epoch*: `{now_utc}`"
    )
    send_telegram_msg(card)
    print(f"[{regime_tag}] {side} @ {spot} | SL: {sl} | TP: {tp}")

    trade_entry = pd.DataFrame([{
        "timestamp": now_utc, "action": side, "price": spot,
        "sl": sl, "tp": tp, "status": "OPEN", "tag": regime_tag
    }])
    trade_entry.to_csv(SCALP_LOG_FILE, mode="a", header=not os.path.exists(SCALP_LOG_FILE), index=False)

def send_telegram_msg(message: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if token and chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=5
            )
        except Exception as e:
            print(f"[TG ERROR] {e}")
  
