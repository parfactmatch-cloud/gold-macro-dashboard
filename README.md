# 🪙 Institutional Macro, MTF Fractal & Order Flow Engine (XAU/USD)

An automated institutional-grade macro bias tracker, multi-timeframe (MTF) fractal exhaustion model, and order flow engine designed specifically for Gold (**XAU/USD**). The system combines multi-variable macroeconomic metrics, liquidity shifts, Binance L2 Order Book (DOM) depth, and a 4-layer fractal equilibrium matrix to deliver high-conviction trade execution alerts directly to Telegram.

---

## 🚀 Live Dashboard & Setup Links

* **Live Dashboard:** [Gold Macro Streamlit Dashboard](https://gold-macro-dashboard.streamlit.app)
* **TradingView Reference:** [XAU/USD Live Chart](https://www.tradingview.com/symbols/XAUUSD/)

---

## 🧠 Core Multi-Layer Architecture

The engine scans and validates 5 core layers before triggering forward-test executions:

| Layer / Model | Data Source / Timeframe | Metric & Logic | Conviction Impact |
| :--- | :--- | :--- | :--- |
| **10Y Real Yield (TIPS)** | FRED (`DFII10`) | 5-Day Delta $< -0.05\%$ (Bullish) / $> +0.05\%$ (Bearish) | $\pm 3$ Macro Points |
| **Fed Net Liquidity** | FRED (`WALCL - TGA - RRP`) | Weekly Expansion (Bullish) / Contraction (Bearish) | $\pm 2$ Macro Points |
| **US Dollar Index (DXY)** | Yahoo Finance (`DX-Y.NYB`) | 5-Day Delta $< -0.50$ (Bullish) / $> +0.50$ (Bearish) | $\pm 2$ Macro Points |
| **Binance Order Book (DOM)** | Binance L2 (`PAXGUSDT`) | Bid/Ask Ratio $\ge 1.25$ (Absorption) / $\le 0.75$ (Distribution) | $\pm 1$ DOM Score |
| **1H Trend Filter** | 1-Hour Chart (`GC=F`) | Spot Price vs 1H 50 EMA & Daily PDH / PDL Breakout Filters | Directional Baseline |

---

## 🏛 Multi-Timeframe Fractal Matrix (MTF Exhaustion)

The system incorporates institutional **Equilibrium (50%) & Range Limit (4X Exhaustion)** theory across 4 synchronized timeframes:

### Setup Conviction Tiers:
* **`STANDARD`:** Macro Score $\ge +4$ (or $\le -4$) + 1H 50 EMA Trend Alignment.
* **`INSTITUTIONAL GRADE`:** Macro Score + 1H Trend + (1H/30M 50% Equilibrium aligned with 15M/5M 4X Exhaustion) + Live DOM Absorption Wall. Provides tighter spread-buffered Stop Loss.

---

## ⚙️ Automation & Forward Testing Workflow

* **Telegram Alerts:** Automated execution signals with precise Entry, Stop Loss (Spread Buffered), Take Profit, and Confluence status.
* **CSV Trade Logging:** Every trigger is appended to `trade_log.csv` for forward testing.
* **Weekly Performance Audit:** `weekly_report.py` audits trade outcomes (Win Rate, Profit Factor, Net Points) every Friday at market close.
* **Execution:** Powered 100% serverless via GitHub Actions.

---

## 🛠 Repository Structure

```text
├── .github/workflows/
│   ├── run_engine.yml          # Hourly macro & fractal scanner
│   └── weekly_performance.yml  # Friday market-close audit dispatch
├── app.py                      # Streamlit interactive dashboard
├── telegram_engine.py          # Core Macro + MTF Fractal + DOM Engine
├── weekly_report.py            # Automated performance analytics & Telegram dispatcher
├── trade_log.csv               # Live forward-tested execution logs
├── requirements.txt            # Python dependencies
└── README.md                   # System documentation
