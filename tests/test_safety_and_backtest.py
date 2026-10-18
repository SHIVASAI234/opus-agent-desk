"""Paper-only safety rules, indicator sanity checks, and backtest integrity."""
import copy

import pandas as pd
import pytest
import yaml

from opus_desk.backtest import run_backtest
from opus_desk.config import SafetyError, enforce_safety, load_config
from opus_desk.data import load_prices
from opus_desk.indicators import atr, rsi


@pytest.mark.parametrize("mode,live", [("live", False), ("paper", True), ("PAPER ", False), ("paper", "false")])
def test_unsafe_config_refuses_to_start(mode, live):
    with pytest.raises(SafetyError):
        enforce_safety({"mode": mode, "allow_live_orders": live})


def test_env_cannot_override_safety(monkeypatch):
    monkeypatch.setenv("ALLOW_LIVE_ORDERS", "true")
    with pytest.raises(SafetyError):
        enforce_safety({"mode": "paper", "allow_live_orders": False})


def test_unsafe_config_file_refuses_to_load(tmp_path, cfg):
    bad = copy.deepcopy(cfg)
    bad["mode"] = "live"
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(bad))
    with pytest.raises(SafetyError):
        load_config(p)


def test_main_exits_on_unsafe_config(tmp_path, cfg):
    from main import main
    bad = copy.deepcopy(cfg)
    bad["allow_live_orders"] = True
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(bad))
    assert main(["--config", str(p)]) == 2


def test_indicators_are_sane(cfg):
    df = load_prices(cfg["data"]["prices_dir"], ["TEST_AAA"])["TEST_AAA"]
    r = rsi(df["close"]).dropna()
    a = atr(df).dropna()
    assert r.between(0, 100).all()
    assert (a > 0).all()


def test_backtest_runs_and_reports_honestly(cfg, tmp_path):
    s = run_backtest(cfg, out_dir=tmp_path)
    assert s["trades"] == len(pd.read_csv(tmp_path / "backtest_trades.csv")) if s["trades"] else True
    assert sum(s["decisions"].values()) == s["trading_days"] - 1
    assert "benchmark_equal_weight_buy_hold_pct" in s
    assert any("Synthetic" in w for w in s["warnings"])
    eq = pd.read_csv(tmp_path / "backtest_equity.csv")
    # No leverage: invested value never exceeds the exposure cap (small tolerance for price drift).
    assert eq["invested_pct"].max() <= cfg["risk"]["max_total_exposure_pct"] + 10
    assert (eq["cash"] >= 0).all()
