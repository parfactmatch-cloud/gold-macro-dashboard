# Institutional Quant Engine: Gold (XAU/USD) Autonomous Execution Pipeline
An institutional-grade, zero-infrastructure algorithmic pipeline engineered for spot Gold (XAU/USD) derivatives analysis and autonomous risk execution. The engine computes dynamic Black-Scholes-Merton (BSM) Gamma Exposure (GEX), evaluates Dealer Delta Exposure (DEX), tracks sovereign macro tailwinds via US 10-Year yields, and autonomously manages simulated paper orders via the **Jev Execution Model** with sub-tick risk controls.
## Architecture Overview
```text
 ┌──────────────────────┐   ┌──────────────────────┐   ┌──────────────────────┐
 │   Twelve Data Core   │   │  London Strat. Edge  │   │  Federal Reserve     │
 │  (Live Interbank XAU)│   │  (LSE Telemetry Core)│   │  (FRED US10Y Yield)  │
 └──────────┬───────────┘   └──────────┬───────────┘   └──────────┬───────────┘
            │                          │                          │
            ▼                          ▼                          ▼
 ┌────────────────────────────────────────────────────────────────────────────┐
 │               free_gex_engine.py (Continuous BSM Greek Core)               │
 │  - Real-time front-month COMEX (GC=F) & ETF Options chain calibration      │
 │  - Call/Put Wall identification, Absolute Gamma Flip, & Total Net DEX      │
 │  - Emits: gex_levels.json                                                  │
 └─────────────────────────────────────┬──────────────────────────────────────┘
                                       │
            ┌──────────────────────────┴──────────────────────────┐
            ▼                                                     ▼
 ┌──────────────────────────────────────┐  ┌──────────────────────────────────┐
 │        telegram_engine.py            │  │       jev_paper_engine.py        │
 │  - FRED US10Y Macro integration      │  │  - Jev Model Entry/Exit Engine   │
 │  - Real-Time Market Radar Pulses     │  │  - Trailing Stop-Loss & BE Shield│
 │  - Gatekeeper Rejection telemetry    │  │  - 2-Way Interactive Bot (/close)│
 │  - Delivery to Telegram Channel/DM   │  │  - Emits: paper_trades.json      │
 └──────────────────────────────────────┘  └──────────────────────────────────┘

```
## Core Capabilities
 * **Dynamic Multi-Source Ingestion**: Primary ingestion through Twelve Data API and London Strategic Edge (LSE) institutional feeds, with automated fallback routing to COMEX Front-Month Futures (GC=F) and Yahoo Forex tape (XAUUSD=X).
 * **Black-Scholes Options Gamma Engine**: Evaluates liquid open interest (OI) and implied volatility (IV) across short-dated maturities (\le 45\text{ DTE}) using normalized standard normal distributions:
   
 * **Jev Model Execution Matrix**:
   * **Put Wall Absorption**: Mean-reversion long execution upon confirmed dealer gamma defense at key floors.
   * **Call Wall Exhaustion**: Structural mean-reversion short positions taken at implied liquidity ceilings.
   * **Institutional Gamma Blast**: Momentum trend participation when market transitions to net short dealer gamma regimes accompanied by expanding directional DEX.
 * **Capital Protection & Trade Management**:
   * Fixed 1.0\% account risk sizing per trade.
   * Automated Breakeven Shield (SL to entry at 1:1.5\text{ R:R}).
   * Scale-out take-profit mechanics (50\% position realized; 50\% runner on dynamic trailing ATR stops).
   * Strict Gatekeeper filtering enforcing a minimum 1:1.30\text{ R:R} ratio.
 * **Interactive Bot Commands**: Send direct commands (/status, /close, /summary) to manage active orders and query win-rate analytics.
## Project Structure
```text
.
├── .github/
│   └── workflows/
│       └── sync.yml              # 15-minute GitHub Actions orchestration cron
├── free_gex_engine.py            # BSM Greek calculation & corridor generation
├── telegram_engine.py            # FRED telemetry & Telegram broadcaster
├── jev_paper_engine.py           # Jev model execution engine & trade ledger
├── gex_levels.json               # Computed institutional gamma barriers (auto-generated)
├── paper_trades.json             # Live paper trading portfolio ledger (auto-generated)
├── requirements.txt              # Core runtime dependencies
└── README.md                     # Production system documentation

```
## Prerequisites & Secret Configuration
Configure the following encrypted credentials in your GitHub repository (**Settings \rightarrow Secrets and variables \rightarrow Actions \rightarrow Repository secrets**):
| Secret Name | Provider / Source | Purpose |
|---|---|---|
| TWELVE_DATA_API_KEY | Twelve Data | Interbank real-time Gold spot tape (XAU/USD) |
| LSE_API_KEY | London Strategic Edge | Institutional order-flow telemetry & status sync |
| FRED_API_KEY | Federal Reserve (St. Louis) | Real-time US 10-Year Treasury Yield (DGS10) |
| TELEGRAM_BOT_TOKEN | Telegram BotFather | Bot authentication token |
| TELEGRAM_CHAT_ID | Telegram | Target Channel or Personal Chat ID for telemetry |
## Installation & Local Development
### 1. Clone & Set Up Environment
```bash
git clone https://github.com/<your-username>/<your-repo-name>.git
cd <your-repo-name>

python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

```
### 2. Environment Variable Setup
Create a .env file in the project root:
```bash
export TWELVE_DATA_API_KEY="your_twelve_data_key"
export LSE_API_KEY="your_lse_api_key"
export FRED_API_KEY="your_fred_key"
export TELEGRAM_BOT_TOKEN="your_bot_token"
export TELEGRAM_CHAT_ID="your_chat_id"

```
Load the variables:
```bash
source .env

```
### 3. Run Pipeline Manually
Execute the sequence in order:
```bash
# 1. Fetch real-time market data & generate GEX levels
python free_gex_engine.py

# 2. Transmit Institutional Radar Pulse to Telegram
python telegram_engine.py

# 3. Process Jev model execution checks & update trade ledger
python jev_paper_engine.py

```
## Continuous Automation (GitHub Actions)
The workflow defined in .github/workflows/sync.yml triggers automatically every 15 minutes (*/15 * * * *) on Ubuntu compute nodes:
 1. **Environment Setup**: Provisions Python 3.10 and dependencies (numpy, pandas, scipy, yfinance, requests).
 2. **Corridor Computation**: Runs free_gex_engine.py using live API secrets.
 3. **Telemetry Broadcast**: Transmits radar updates and macro indicators via telegram_engine.py.
 4. **Order Execution**: jev_paper_engine.py evaluates positions and executes paper orders.
 5. **Ledger Commit**: Automatically commits mutated gex_levels.json and paper_trades.json back to the default branch using git-auto-commit-action.
## Telegram Interactive Bot Reference
Send commands directly to your configured Telegram bot:
 * /status — Displays current active trade metrics, entry price, stop-loss level, floating PnL, and Breakeven Shield status.
 * /close — Immediately closes any open paper trade at the current spot price and records realized PnL.
 * /summary — Outputs performance analytics, including cumulative balance, win/loss count, and percentage win rate.
## Risk Disclaimer
> **Notice**: This repository is designed for quantitative research, mathematical modeling, and paper-trading simulation only. Financial market trading, particularly in leveraged derivatives and spot commodities (XAU/USD), carries substantial capital risk. Test all strategies thoroughly in paper simulation before using them with live capital.
>
> 
