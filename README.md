# 🪙 Institutional Macro, Real-Time Tick WebSocket & Dynamic Quant Engine

An automated institutional-grade macro bias tracker and sub-second real-time execution engine designed specifically for Gold (**XAU/USD**). The system combines multi-variable macroeconomic regime tracking with a high-frequency WebSocket tick engine (`wss://data-ws.londonstrategicedge.com`), eliminating cron-based lag through 100% dynamic volatility envelopes (ATR multiples), liquidity sweep detection, and exhaustion fade architecture.

---

## 🚀 Live Dashboard & Reference Links

* **Live Dashboard:** [Gold Macro Streamlit Dashboard](https://gold-macro-dashboard.streamlit.app)
* **TradingView Reference:** [XAU/USD Live Chart](https://www.tradingview.com/symbols/XAUUSD/)

---

## 🧠 Multi-Layer Macro & High-Frequency Architecture

The engine couples broad macro-regime filtering with sub-second order book and tick execution:

| Layer / Model | Data Source / Timeframe | Metric & Logic | Conviction / Action |
| :--- | :--- | :--- | :--- |
| **10Y Real Yield (TIPS / US10Y)** | FRED (`DFII10`) / Macro Feed | Yield Delta $> 0$ imposes **BEARISH_PRESSURE**; Hard-gates long breakouts | Hard Directional Gate |
| **Fed Net Liquidity** | FRED (`WALCL - TGA - RRP`) | Weekly Expansion (Bullish) / Contraction (Bearish) | $\pm 2$ Macro Points |
| **US Dollar Index (DXY)** | Yahoo Finance (`DX-Y.NYB`) | 5-Day Momentum Vector Delta | $\pm 2$ Macro Points |
| **LSE Real-Time WebSocket** | London Strategic Edge (`XAU/USD`) | Sub-second raw tick streaming with zero network-proxy latency | Real-Time Engine Feed |
| **Dynamic Quant Kernel** | 5M Synthesized Bars + Live Tick | Adaptive ATR normalized deviation, candle wick geometry, and moving equilibrium | Zero-Hardcoding Execution |

---

## ⚡ Real-Time Dynamic Quant Models (`dynamic_engine.py`)

The engine bypasses static dollar targets and rigid price levels, adapting strictly to floating ATR volatility and market microstructure:


