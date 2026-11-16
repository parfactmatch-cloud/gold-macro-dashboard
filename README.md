# Gold Macro Dashboard: XAU/USD Paper-Trading Pipeline

A gold (XAU/USD) macro + options-gamma dashboard with a **paper-trading**
engine. There is no broker integration and no live execution anywhere in
this repository — the Jev engine paper-trades spot gold with virtual money.

> ⚠️ **Security notice (Nov 2026):** a FRED API key was previously
> hardcoded in `app.py` in this public repo. It has been removed — the key
> now comes from the `FRED_API_KEY` environment variable / GitHub Secret.
> **If you used that key anywhere else, rotate/revoke it in FRED** — it
> was visible in the public git history and cannot be un-exposed by this
> fix alone.

## What it is — honestly

- **Macro bias score** (`app.py`, Streamlit): US 10Y real yields, Fed net
  liquidity, DXY, CFTC positioning, intermarket ratios. Max ±8.
- **Dealer gamma levels** (`free_gex_engine.py`): Black-Scholes gamma from
  the GLD options chain (a proxy for XAU), giving call wall / put wall /
  gamma flip / net DEX, plus a session volume profile from COMEX GC=F.
- **Kronos regime check** (`kronos_gatekeeper.py`): a **10-bar momentum
  sigmoid, not a neural network**. It loads no model weights. Treat its
  probability as a coarse directional filter.
- **Jev paper engine** (`jev_paper_engine.py`): opens/manages one paper
  position at a time with stop-loss, targets and a 50% scale-out at 1.5R,
  gated by GEX walls, DOM/CVD, LBMA auction timing, TPO guards, Kronos
  and a news blackout. Risk limits: max daily loss $500, max 3 trades/day,
  balance floor $9,000 (see `config.py`; overridable by env vars).
- **Telegram** (`telegram_engine.py` + `live_telegram_listener.py`):
  radar/playbook broadcasts and commands. Commands are answered by ONE
  shared poller (`telegram_commands.py`, shared offset in
  `telegram_offset.json`) and **only from the configured
  `TELEGRAM_CHAT_ID`** — anyone else's commands are ignored.
- **Automation** (`.github/workflows/sync.yml`): every 15 minutes it runs
  news → GEX → Kronos → Jev → Telegram and commits the state JSONs back
  to `main`.

## Known limitations (read before trusting the output)

- GEX levels are computed from GLD options, not XAU options, and the
  volume profile is a $1-bin approximation. When a feed fails, the engine
  writes **synthetic** levels labelled `status: "DEGRADED_SYNTHETIC"` and
  `synthetic: true` — the Jev engine will not open new trades off them.
- Hybrid GEX (default, `GEX_SOURCE=hybrid`): gamma comes from the free
  London Strategic Edge GLD options chain, open interest from yfinance —
  LSE's chain has no open-interest field, so GEX cannot be built from LSE
  alone (verified 16 Nov 2026). If the LSE call fails, the engine falls
  back to pure yfinance BSM gamma and logs the failure. COMEX OG options
  are not used: LSE lists them, but prices need a paid CME Group Lite plan.
  Live LSE spot comes from the LSE WebSocket, not its HTTP candles, which
  are end-of-day stored data.
- "LSE institutional" telemetry is only as real as your `LSE_API_KEY`.
  With no key it reports `UNAVAILABLE`, not ONLINE.
- The HTML terminal's WebSocket (`index.html`) is an unauthenticated
  public feed with no subscription — it is not a verified live stream.
- The weekly report (`weekly_report.py`) reads the real paper-trades
  ledger; run it manually or on a weekly schedule, not in the 15-minute loop.
- There is no backtester and no test coverage of live data feeds; the
  test suite covers logic only, with mocked data.

## Project structure

```text
.
├── .github/workflows/sync.yml     # 15-minute GitHub Actions orchestration
├── app.py                         # Streamlit dashboard
├── index.html                     # Static ARES-XAU web terminal
├── free_gex_engine.py             # BSM gamma / volume profile
├── kronos_gatekeeper.py           # Momentum regime check (not a neural model)
├── jev_paper_engine.py            # Paper-trading engine & ledger
├── telegram_engine.py             # Radar/playbook broadcaster + command handler
├── telegram_commands.py           # Shared Telegram polling + sender verification
├── live_telegram_listener.py      # Optional 24/7 Telegram listener (same shared offset)
├── config.py                      # Central config & risk limits
├── order_flow_dom_engine.py       # DOM proxy + real CVD telemetry
├── profile_tpo_guard_engine.py    # Market-profile / TPO guards
├── lbma_benchmark_engine.py       # LBMA auction timing (London-time windows)
├── news_gatekeeper.py             # High-impact USD news blackout
├── weekly_report.py               # Weekly audit from the paper-trades ledger
├── gex_levels.json                # Computed gamma levels (auto-generated)
├── paper_trades.json              # Paper-trading ledger (auto-generated)
├── requirements.txt               # Pinned runtime dependencies
├── requirements-dev.txt           # Test dependencies
├── .env.example                   # Copy to .env (never commit .env)
└── README.md
```

## Prerequisites & secret configuration

Set these as GitHub repository Secrets (**Settings → Secrets and
variables → Actions → Repository secrets**) or in a local `.env` file
(see `.env.example` — never commit `.env`):

| Secret Name | Purpose |
|---|---|
| TWELVE_DATA_API_KEY | Twelve Data real-time gold spot (XAU/USD) |
| LSE_API_KEY | London Strategic Edge — live XAU/USD via WebSocket and GLD gamma for hybrid GEX (optional; without it GEX falls back to yfinance BSM and LSE shows UNAVAILABLE) |
| GEX_SOURCE | `hybrid` (default: LSE gamma + yfinance OI) or `yfinance` (BSM gamma, no LSE call) |
| FRED_API_KEY | FRED US 10-Year Treasury Yield (DGS10) — **rotate the old hardcoded key** |
| TELEGRAM_BOT_TOKEN | Telegram BotFather token |
| TELEGRAM_CHAT_ID | The one chat allowed to send commands to the bot |

## Installation & local development

```bash
git clone https://github.com/parfactmatch-cloud/gold-macro-dashboard.git
cd gold-macro-dashboard

python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
source .env
```

Run the pipeline manually, in order:

```bash
python free_gex_engine.py      # GEX levels -> gex_levels.json
python kronos_gatekeeper.py    # momentum regime -> kronos_signal.json
python news_gatekeeper.py      # news blackout -> news_status.json
python jev_paper_engine.py     # paper-trade cycle -> paper_trades.json
python telegram_engine.py      # radar/playbook + Telegram commands
```

Run the dashboard:

```bash
streamlit run app.py
```

Run the tests:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

## Telegram commands

Send commands to your bot from the configured chat only:

- `/levels` — call/put walls, gamma flip, volume profile
- `/status` — active paper trade, entry, SL/TP, floating PnL
- `/close` — close the open paper trade at the current spot
- `/pause` / `/resume` — stop / restart fresh entries (open positions are still managed)
- `/pnl` / `/summary` — balance, cumulative PnL, win rate
- `/kronos` / `/ai` — Kronos momentum regime and probability

## Risk disclaimer

> This repository is for quantitative research, mathematical modeling,
> and paper-trading simulation only. Financial market trading, particularly
> in leveraged derivatives and spot commodities (XAU/USD), carries
> substantial capital risk. Test all strategies thoroughly in paper
> simulation before using them with live capital.
