"""Tests for the gold-macro-dashboard fixes.

No test hits a live API or Telegram. Network calls are monkeypatched
or avoided entirely; data is synthetic.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import telegram_commands
import jev_paper_engine as jev
import order_flow_dom_engine as dom
import free_gex_engine as gex
import kronos_gatekeeper as kronos
import weekly_report as weekly
import live_telegram_listener as listener


# ---------------------------------------------------------------- helpers
def _bars(rows):
    return pd.DataFrame(rows, columns=["Open", "High", "Low", "Close", "Volume"])


def _rising_bars(n=15):
    rows = []
    price = 3800.0
    for i in range(n):
        rows.append(
            {
                "Open": price,
                "High": price + 4.0,
                "Low": price - 1.0,
                "Close": price + 3.0,
                "Volume": 100.0,
            }
        )
        price += 3.0
    return _bars(rows)


def _divergence_bars():
    # First 3 bars: hard selling, CVD deeply negative, lows at 90.
    # Last 3 bars: lower lows (85) but buying volume, CVD recovering.
    rows = [
        {"Open": 100.0, "High": 100.0, "Low": 90.0, "Close": 90.0, "Volume": 1000.0},
        {"Open": 100.0, "High": 100.0, "Low": 90.0, "Close": 90.0, "Volume": 1000.0},
        {"Open": 100.0, "High": 100.0, "Low": 90.0, "Close": 90.0, "Volume": 1000.0},
        {"Open": 89.0, "High": 89.5, "Low": 85.0, "Close": 88.5, "Volume": 1000.0},
        {"Open": 89.0, "High": 89.5, "Low": 85.0, "Close": 88.5, "Volume": 1000.0},
        {"Open": 89.0, "High": 89.5, "Low": 85.0, "Close": 88.5, "Volume": 1000.0},
    ]
    return _bars(rows)


# ---------------------------------------------------------------- (a) FRED key
def test_no_hardcoded_fred_key_in_app():
    app_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")
    text = open(app_path).read()
    assert 'os.getenv("FRED_API_KEY"' in text
    # No 32-hex-char literal anywhere (the old hardcoded key format).
    assert not re.search(r'["\'][0-9a-fA-F]{32}["\']', text)


# ---------------------------------------------------------------- (b) Telegram auth
def test_sender_verification_accepts_configured_chat():
    assert telegram_commands.sender_is_authorized(
        {"chat": {"id": 12345}}, chat_id="12345"
    )
    assert telegram_commands.sender_is_authorized(
        {"chat": {"id": "12345"}}, chat_id="12345"
    )


def test_sender_verification_rejects_strangers_and_empty_config():
    assert not telegram_commands.sender_is_authorized(
        {"chat": {"id": 99999}}, chat_id="12345"
    )
    assert not telegram_commands.sender_is_authorized(
        {"chat": {"id": 12345}}, chat_id=""
    )
    assert not telegram_commands.sender_is_authorized({}, chat_id="12345")


def test_fetch_new_updates_ignores_unauthorized(monkeypatch, tmp_path):
    """Unauthorized updates are returned with authorized=False, and the
    shared offset still advances so they are not re-processed."""
    monkeypatch.setattr(telegram_commands, "OFFSET_FILE", str(tmp_path / "offset.json"))
    monkeypatch.setattr(telegram_commands, "TELEGRAM_CHAT_ID", "12345")

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "result": [
                    {"update_id": 10, "message": {"chat": {"id": 99999}, "text": "/close"}},
                    {"update_id": 11, "message": {"chat": {"id": 12345}, "text": "/status"}},
                ]
            }

    monkeypatch.setattr(telegram_commands.requests, "get", lambda *a, **k: FakeResp())
    updates = telegram_commands.fetch_new_updates(token="dummy")
    assert len(updates) == 2
    assert updates[0][1] is False
    assert updates[1][1] is True
    assert telegram_commands.load_offset() == 11


# ---------------------------------------------------------------- (c) CVD telemetry
def test_cvd_telemetry_present_and_rising_on_up_bars():
    result = dom.analyze_l2_l3_microstructure(_rising_bars())
    cvd = result["cvd_telemetry"]
    assert cvd["cvd"] > 0
    assert cvd["cvd_trend"] == "RISING"
    assert cvd["cvd_divergence"] in ("NONE", "BULLISH_ABSORPTION_DIV", "BEARISH_EXHAUSTION_DIV")


def test_cvd_bullish_absorption_divergence_detected():
    result = dom.analyze_l2_l3_microstructure(_divergence_bars())
    cvd = result["cvd_telemetry"]
    assert cvd["cvd_trend"] == "RISING"
    assert cvd["cvd_divergence"] == "BULLISH_ABSORPTION_DIV"


def test_cvd_telemetry_present_on_empty_data():
    result = dom.analyze_l2_l3_microstructure(pd.DataFrame())
    assert result["cvd_telemetry"] == {
        "cvd": 0.0,
        "cvd_trend": "FLAT",
        "cvd_divergence": "NONE",
    }


# ------------------------------------------------------- (d) synthetic labelling
def test_gex_fallback_is_flagged_synthetic(monkeypatch):
    """When the options chain cannot be fetched, the fallback is flagged."""
    class DeadTicker:
        def __init__(self, *a, **k):
            pass

        def history(self, *a, **k):
            raise RuntimeError("no network in tests")

    monkeypatch.setattr(gex.yf, "Ticker", DeadTicker)
    out = gex.compute_options_gex(3880.0)
    assert out["synthetic"] is True
    assert out["call_wall_xau"] == 3915.0  # spot + 35
    assert out["put_wall_xau"] == 3845.0   # spot - 35


def test_gex_main_marks_synthetic_status(monkeypatch, tmp_path):
    monkeypatch.setattr(gex, "OUTPUT_FILE", str(tmp_path / "gex_levels.json"))
    monkeypatch.setattr(gex, "fetch_spot_gold", lambda: (3880.0, "STATIC_SYNTHETIC_FALLBACK"))
    monkeypatch.setattr(
        gex, "compute_options_gex",
        lambda spot: {"gld_close": 355.0, "conv_ratio": 10.94, "call_wall_xau": 3915.0,
                      "put_wall_xau": 3845.0, "gamma_flip_xau": 3868.0, "net_dex_m": -2.5,
                      "net_gamma_regime": "LONG_GAMMA_MEAN_REVERT", "gamma_blast_active": False,
                      "synthetic": True},
    )
    monkeypatch.setattr(
        gex, "compute_volume_profile",
        lambda spot: {"poc": 3880.0, "vah": 3895.0, "val": 3865.0, "profile_status": "SYNTHETIC_ESTIMATE"},
    )
    gex.main()
    payload = json.loads((tmp_path / "gex_levels.json").read_text())
    assert payload["status"] == "DEGRADED_SYNTHETIC"
    assert payload["synthetic"] is True
    assert payload["is_corridor_valid"] is False
    assert payload["lse_telemetry"]["lse_status"] == "UNAVAILABLE"


def test_gex_main_healthy_when_live(monkeypatch, tmp_path):
    monkeypatch.setattr(gex, "OUTPUT_FILE", str(tmp_path / "gex_levels.json"))
    monkeypatch.setattr(gex, "LSE_API_KEY", "test-key")
    monkeypatch.setattr(gex, "fetch_spot_gold", lambda: (3880.0, "LSE_INSTITUTIONAL"))
    monkeypatch.setattr(
        gex, "compute_options_gex",
        lambda spot: {"gld_close": 355.0, "conv_ratio": 10.94, "call_wall_xau": 3930.0,
                      "put_wall_xau": 3820.0, "gamma_flip_xau": 3870.0, "net_dex_m": 1.2,
                      "net_gamma_regime": "LONG_GAMMA_MEAN_REVERT", "gamma_blast_active": False,
                      "synthetic": False},
    )
    monkeypatch.setattr(
        gex, "compute_volume_profile",
        lambda spot: {"poc": 3880.0, "vah": 3895.0, "val": 3865.0, "profile_status": "CALCULATED"},
    )
    gex.main()
    payload = json.loads((tmp_path / "gex_levels.json").read_text())
    assert payload["status"] == "HEALTHY"
    assert payload["synthetic"] is False
    assert payload["is_corridor_valid"] is True
    assert payload["lse_telemetry"]["lse_status"] == "ONLINE"


def test_norm_pdf_uses_public_scipy_api():
    import inspect

    src = inspect.getsource(gex.norm_pdf)
    assert "norm._pdf(" not in src  # the private call must be gone
    assert "norm.pdf(x)" in src
    assert abs(gex.norm_pdf(0.0) - 0.3989422804014327) < 1e-9


# ---------------------------------------------------------------- (e) raw URL
def test_listener_default_raw_base_is_parfactmatch():
    assert "parfactmatch-cloud" in listener.GITHUB_RAW_BASE
    assert "surfactmatch" not in listener.GITHUB_RAW_BASE


# ------------------------------------------------------- (f) ledger reconciliation
def _open_trade_state():
    return {
        "balance": 10000.0,
        "active_trade": {
            "trade_id": "JEV-1",
            "side": "BUY",
            "entry_price": 4000.0,
            "units": 10.0,
            "initial_risk": 10.0,
            "sl": 3990.0,
            "tp": 4030.0,
            "partial_pnl": 0.0,
            "partial_taken": False,
        },
        "closed_trades": [],
        "daily": {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                  "trades_opened": 1, "daily_pnl": 0.0},
    }


def test_scale_out_then_close_reconciles(tmp_path, monkeypatch):
    """Balance change must equal the closed trade's PnL (scale-out included)."""
    monkeypatch.setattr(jev, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    state = _open_trade_state()
    start_balance = state["balance"]

    # Simulate the 50% scale-out at +$15/oz on 5 of 10 units = +$75.
    active = state["active_trade"]
    partial = round(5.0 * 15.0, 2)
    state["balance"] += partial
    active["partial_pnl"] = partial
    active["units"] = 5.0
    active["partial_taken"] = True

    closed = jev.close_active_trade(state, spot=4010.0)
    # Remaining 5 units at +$10 = +$50; total +$125.
    assert closed["pnl"] == 125.0
    assert state["balance"] == start_balance + 125.0
    assert state["balance"] - start_balance == sum(
        t["pnl"] for t in state["closed_trades"]
    )


def test_close_without_partial_reconciles(tmp_path, monkeypatch):
    monkeypatch.setattr(jev, "TRADES_FILE", str(tmp_path / "paper_trades.json"))
    state = _open_trade_state()
    closed = jev.close_active_trade(state, spot=3990.0)
    assert closed["pnl"] == -100.0
    assert state["balance"] == 9900.0


# ---------------------------------------------------------------- (g) risk limits
def test_risk_limits_defaults():
    assert config.MAX_DAILY_LOSS_USD == 500
    assert config.MAX_TRADES_PER_DAY == 3
    assert config.BALANCE_FLOOR_USD == 9000
    assert config.MAX_OPEN_POSITIONS == 1


def test_daily_loss_limit_blocks():
    state = _open_trade_state()
    state["daily"]["daily_pnl"] = -600.0
    assert "daily loss" in jev.risk_limits_blocked(state)


def test_max_trades_per_day_blocks():
    state = _open_trade_state()
    state["daily"]["trades_opened"] = 3
    assert "max trades" in jev.risk_limits_blocked(state)


def test_balance_floor_blocks():
    state = _open_trade_state()
    state["balance"] = 8500.0
    assert "balance floor" in jev.risk_limits_blocked(state)


def test_no_block_when_within_limits():
    state = _open_trade_state()
    assert jev.risk_limits_blocked(state) == ""


def test_daily_stats_reset_on_new_day():
    state = _open_trade_state()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    state["daily"] = {"date": yesterday, "trades_opened": 3, "daily_pnl": -600.0}
    assert jev.risk_limits_blocked(state) == ""


# ---------------------------------------------------------------- (h) Kronos honesty
def test_kronos_has_no_torch_dependency():
    src = open(kronos.__file__).read()
    assert "import torch" not in src
    assert "from torch" not in src
    # The docstring may mention torch only to say it was removed.
    assert src.count("torch") <= 2


def test_kronos_momentum_probability(monkeypatch, tmp_path):
    """A +1% 10-bar move gives a high bull probability via the sigmoid."""
    import numpy as np

    klines = np.zeros((64, 5))
    klines[:, 3] = 4000.0
    klines[-10:, 3] = 4000.0
    klines[-1, 3] = 4040.0  # +1% over the last 10 bars
    monkeypatch.setattr(kronos, "fetch_recent_klines", lambda *a, **k: (klines, 4040.0))
    monkeypatch.setattr(kronos, "OUTPUT_AI_FILE", str(tmp_path / "kronos_signal.json"))
    kronos.run_kronos_inference()
    payload = json.loads((tmp_path / "kronos_signal.json").read_text())
    assert payload["model"] == "momentum_sigmoid_10bar"
    assert payload["predicted_regime"] == "BULLISH_EXPANSION"
    assert payload["bullish_probability"] > 0.8


def test_kronos_data_unavailable_is_neutral(monkeypatch, tmp_path):
    monkeypatch.setattr(kronos, "fetch_recent_klines", lambda *a, **k: (None, 0.0))
    monkeypatch.setattr(kronos, "OUTPUT_AI_FILE", str(tmp_path / "kronos_signal.json"))
    kronos.run_kronos_inference()
    payload = json.loads((tmp_path / "kronos_signal.json").read_text())
    assert payload["predicted_regime"] == "NEUTRAL"
    assert payload["bullish_probability"] == 0.50


# ---------------------------------------------------------------- (i) weekly report
def test_weekly_report_uses_real_ledger(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    trades = {
        "balance": 10100.0,
        "active_trade": None,
        "closed_trades": [
            {"trade_id": f"JEV-{int((now - timedelta(days=1)).timestamp())}",
             "side": "BUY", "entry_price": 4000.0, "sl": 3990.0, "tp": 4030.0,
             "pnl": 50.0, "exit_reason": "TARGET_HIT_POC"},
            {"trade_id": f"JEV-{int((now - timedelta(days=2)).timestamp())}",
             "side": "SELL", "entry_price": 4010.0, "sl": 4020.0, "tp": 3990.0,
             "pnl": -30.0, "exit_reason": "STOP_LOSS_HIT"},
            # 30 days ago: outside the 7-day window, must be excluded.
            {"trade_id": f"JEV-{int((now - timedelta(days=30)).timestamp())}",
             "side": "BUY", "entry_price": 4000.0, "sl": 3990.0, "tp": 4030.0,
             "pnl": 999.0, "exit_reason": "TARGET_HIT_POC"},
        ],
    }
    ledger = tmp_path / "paper_trades.json"
    ledger.write_text(json.dumps(trades))
    monkeypatch.setattr(weekly, "TRADES_FILE", str(ledger))
    monkeypatch.setattr(weekly, "SCALP_LOG_FILE", str(tmp_path / "nope.csv"))
    monkeypatch.setattr(weekly, "TRADE_LOG_FILE", str(tmp_path / "nope.csv"))

    df = weekly.load_and_clean_logs(now - timedelta(days=7), now)
    assert len(df) == 2
    assert sorted(df["pnl"].tolist()) == [-30.0, 50.0]


def test_weekly_report_empty_state_is_honest(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(weekly, "TRADES_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(weekly, "SCALP_LOG_FILE", str(tmp_path / "nope.csv"))
    monkeypatch.setattr(weekly, "TRADE_LOG_FILE", str(tmp_path / "nope.csv"))
    monkeypatch.setattr(weekly, "dispatch_telegram", lambda text: False)
    with caplog.at_level("INFO", logger="weekly_report"):
        weekly.generate_weekly_report()
    assert "No closed trades in the window" in caplog.text
    assert "successfully prevented" not in caplog.text


# ---------------------------------------------------------------- (k) LBMA docstring
def test_lbma_docstring_matches_london_time_windows():
    import lbma_benchmark_engine as lbma

    doc = lbma.inspect_lbma_auction_window.__doc__
    assert "London time" in doc
    assert "10:05 - 10:29 London" in doc
    assert "3:05 PM - 3:29 PM IST" not in doc


# ---------------------------------------------------------------- misc
def test_paper_mode_defaults_and_no_live_execution():
    # The repo must not contain any broker order-placement code.
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in os.listdir(repo):
        if name.endswith(".py"):
            text = open(os.path.join(repo, name)).read()
            assert "place_order" not in text
            assert "create_order" not in text


# ---------------------------------------------------------------- (l) LSE hybrid GEX
def _fake_gld_ticker(monkeypatch, expiry="2026-12-18"):
    """GLD ticker with a 355 close and one expiry, OI 1000 on each side."""
    import numpy as _np

    class FakeChain:
        def __init__(self, strikes):
            self.calls = pd.DataFrame(
                {"strike": strikes, "openInterest": [1000] * len(strikes),
                 "impliedVolatility": [0.20] * len(strikes)}
            )
            self.puts = pd.DataFrame(
                {"strike": strikes, "openInterest": [1000] * len(strikes),
                 "impliedVolatility": [0.20] * len(strikes)}
            )

    class FakeTicker:
        def __init__(self, *a, **k):
            pass

        def history(self, *a, **k):
            return pd.DataFrame({"Close": [355.0, 355.0]})

        @property
        def options(self):
            return [expiry]

        def option_chain(self, exp):
            return FakeChain([350.0, 355.0, 360.0])

    monkeypatch.setattr(gex.yf, "Ticker", FakeTicker)
    return expiry


def test_hybrid_gex_uses_lse_gamma_and_yfinance_oi(monkeypatch):
    expiry = _fake_gld_ticker(monkeypatch)
    monkeypatch.setattr(gex, "LSE_API_KEY", "test-key")
    monkeypatch.setattr(gex, "GEX_SOURCE", "hybrid")

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "contracts": 6,
                "data": [
                    {"expiry": expiry, "strike": 350.0, "contract_type": "call",
                     "gamma": 0.02, "delta": 0.6, "iv": 0.20, "dte": 32},
                    {"expiry": expiry, "strike": 355.0, "contract_type": "call",
                     "gamma": 0.03, "delta": 0.5, "iv": 0.20, "dte": 32},
                    {"expiry": expiry, "strike": 360.0, "contract_type": "call",
                     "gamma": 0.01, "delta": 0.4, "iv": 0.20, "dte": 32},
                    {"expiry": expiry, "strike": 350.0, "contract_type": "put",
                     "gamma": 0.02, "delta": -0.4, "iv": 0.20, "dte": 32},
                    {"expiry": expiry, "strike": 355.0, "contract_type": "put",
                     "gamma": 0.03, "delta": -0.5, "iv": 0.20, "dte": 32},
                    {"expiry": expiry, "strike": 360.0, "contract_type": "put",
                     "gamma": 0.01, "delta": -0.6, "iv": 0.20, "dte": 32},
                ],
            }

    monkeypatch.setattr(gex.requests, "get", lambda *a, **k: FakeResp())
    out = gex.compute_options_gex(3880.0)
    assert out["synthetic"] is False
    assert out["gex_source"] == "LSE_HYBRID"
    assert out["gex_gamma_source"] == "LSE"
    assert out["gex_oi_source"] == "YFINANCE"
    assert out["lse_contracts_used"] == 6
    assert out["lse_gamma_contracts"] == 6
    assert out["call_wall_xau"] > out["put_wall_xau"]


def test_hybrid_gex_falls_back_to_yfinance_when_lse_fails(monkeypatch):
    _fake_gld_ticker(monkeypatch)
    monkeypatch.setattr(gex, "LSE_API_KEY", "test-key")
    monkeypatch.setattr(gex, "GEX_SOURCE", "hybrid")
    monkeypatch.setattr(
        gex.requests, "get",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("LSE down")),
    )
    out = gex.compute_options_gex(3880.0)
    assert out["synthetic"] is False
    assert out["gex_source"] == "YFINANCE_BSM"
    assert out["lse_contracts_used"] == 0


def test_hybrid_gex_skipped_without_key(monkeypatch):
    _fake_gld_ticker(monkeypatch)
    monkeypatch.setattr(gex, "LSE_API_KEY", "")
    monkeypatch.setattr(gex, "GEX_SOURCE", "hybrid")
    out = gex.compute_options_gex(3880.0)
    assert out["gex_source"] == "YFINANCE_BSM"


def test_lse_chain_parser_handles_list_payload(monkeypatch):
    monkeypatch.setattr(gex, "LSE_API_KEY", "test-key")

    class FakeResp:
        status_code = 200

        def json(self):
            return [{"expiry": "2026-12-18", "strike": 355.0}]

    monkeypatch.setattr(gex.requests, "get", lambda *a, **k: FakeResp())
    rows = gex.fetch_lse_gld_chain("test-key")
    assert rows and rows[0]["strike"] == 355.0
