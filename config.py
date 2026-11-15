"""Central configuration for the gold-macro-dashboard paper-trading system.

All values come from environment variables with safe defaults. Nothing here
is a secret: API keys and tokens must be supplied via the environment
(see .env.example) and are never stored in this file.

This is a PAPER-TRADING system only. There is no broker integration and
no live execution anywhere in this repository.
"""

import logging
import os

# --- Trading / risk limits (Jev paper engine) ---------------------------
STARTING_BALANCE_USD = float(os.getenv("STARTING_BALANCE_USD", "10000"))
MAX_DAILY_LOSS_USD = float(os.getenv("MAX_DAILY_LOSS_USD", "500"))
MAX_TRADES_PER_DAY = int(os.getenv("MAX_TRADES_PER_DAY", "3"))
BALANCE_FLOOR_USD = float(os.getenv("BALANCE_FLOOR_USD", "9000"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "1"))

# Base risk per trade (fraction of balance); high-conviction / neutral
# adjustments are applied on top of these in jev_paper_engine.py.
BASE_RISK_PCT = float(os.getenv("BASE_RISK_PCT", "0.010"))
HIGH_CONVICTION_RISK_PCT = float(os.getenv("HIGH_CONVICTION_RISK_PCT", "0.015"))
NEUTRAL_RISK_PCT = float(os.getenv("NEUTRAL_RISK_PCT", "0.0075"))
MIN_RR = float(os.getenv("MIN_RR", "1.30"))

# --- Kronos momentum regime check ---------------------------------------
# Kronos is a 10-bar momentum sigmoid, NOT a neural / foundation model.
# See kronos_gatekeeper.py. The probability is deliberately saturated:
# a 0.5% 10-bar move already gives ~0.68, so treat it as a coarse filter.
KRONOS_MODEL_LABEL = "momentum_sigmoid_10bar"

# --- Logging --------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def setup_logging(name: str = "gold_macro") -> logging.Logger:
    """Return a module logger configured once with a plain text handler."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    logger.propagate = False
    return logger
