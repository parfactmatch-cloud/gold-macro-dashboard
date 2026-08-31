# gold-macro-dashboard
# 🪙 Institutional Macro & Order Flow Gold Engine (XAU/USD)

An automated institutional-grade macro bias tracker, order flow imbalance engine, and forward-testing framework designed specifically for Gold (XAU/USD). The system tracks multi-variable macroeconomic metrics, liquidity shifts, commitment of traders (COT) data, live Order Book (DOM) depth, and 1-hour trend alignment to deliver structured trade execution alerts directly to Telegram.

---

## 🚀 Live Dashboard & Setup Links
* **Live Dashboard:** [Gold Macro Streamlit Dashboard](https://gold-macro-dashboard-5vuz6kxl25bappkbsjnisr7.streamlit.app/)
* **TradingView Chart:** [XAU/USD on TradingView](https://in.tradingview.com/chart/?symbol=OANDA:XAUUSD)

---

## 🧠 Core Architecture & Scoring Model (-9 to +9)

The engine evaluates 5 macro and microstructure layers before triggering high-probability executions:

| Indicator / Layer | Source | Metric Weight | Bullish Bias Condition | Bearish Bias Condition |
| :--- | :--- | :--- | :--- | :--- |
| **10Y Real Yield (TIPS)** | FRED (`DFII10`) | $\pm 3$ | 5-Day Delta $< -0.05\%$ | 5-Day Delta $> +0.05\%$ |
| **Fed Net Liquidity** | FRED (`WALCL - TGA - RRP`) | $\pm 2$ | Weekly Delta $> 0$ | Weekly Delta $< 0$ |
| **US Dollar Index (DXY)** | Yahoo Finance (`DX-Y.NYB`)| $\pm 2$ | 5-Day Delta $< -0.50$ | 5-Day Delta $> +0.50$ |
| **CFTC COT Flow** | CFTC Public API | $\pm 1$ | Managed Money Net Long Increase | Managed Money Net Short Increase |
| **Order Book Depth (DOM)**| Public L2 API (`PAXGUSDT`)| $\pm 1$ | Bid/Ask Ratio $\ge 1.25$ | Bid/Ask Ratio $\le 0.75$ |

### 🎯 Technical & Execution Filter
* **Trend Alignment:** 1-Hour Gold Spot Price vs 50 EMA.
* **Buy Trigger:** Macro + DOM Score $\ge +4$ **AND** 1H Close $>$ 1H 50 EMA.
* **Sell Trigger:** Macro + DOM Score $\le -4$ **AND** 1H Close $<$ 1H 50 EMA.
* **Spread Protection:** 25/75 volatility spread buffer added to stop-loss and take-profit calculations.
* **Fixed Risk Sizing:** 1% fixed account risk dynamically calculated based on actual point distance.

---

## ⚙️ Repository Structure

├── .github/
│   └── workflows/
│       ├── main.yml             # Hourly automated macro/DOM scan engine
│       └── weekly_summary.yml   # Friday market close weekly performance report
├── app.py                       # Streamlit interactive web dashboard
├── telegram_engine.py           # Real-time data pipeline & alert trigger engine
├── weekly_report.py             # Performance aggregator & CSV analyzer
├── trade_log.csv                # Historical execution & paper test journal
├── requirements.txt             # Project dependencies
└── README.md                    # System documentation

Install dependencies:
pip install -r requirements.txt

Run Streamlit Dashboard locally:

streamlit run app.py

Test the Macro & Telegram Engine:
python telegram_engine.py

📊 Automated Reports & Forward Testing
Hourly Alerts: Runs every hour during active market sessions (Monday - Friday) via GitHub Actions.
Weekly Performance Audit: Every Friday at market close (22:00 UTC), generating win rate, aggregate risk-to-reward ratio, and trade counts directly to Telegram.

