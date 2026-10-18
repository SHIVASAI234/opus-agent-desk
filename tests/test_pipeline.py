"""End-to-end pipeline behaviour: ordering, stale data, risk veto, missing sources, Boss limits."""
import copy
from pathlib import Path

import pandas as pd

from opus_desk.agents import Position, boss
from opus_desk.pipeline import build_context, run_pipeline
from opus_desk.schema import AGENT_ORDER, validate_report

APPROVE_DATE = "2026-09-17"       # TEST_DDD breakout, no blocking event (synthetic fixtures)
EVENT_REJECT_DATE = "2026-09-22"  # TEST_DDD breakout, but earnings on 2026-10-06


def test_six_reports_in_order_and_all_valid(cfg):
    result = run_pipeline(build_context(cfg, as_of=APPROVE_DATE))
    assert list(result["reports"]) == AGENT_ORDER
    for rep in result["reports"].values():
        validate_report(rep)
    assert result["decision"] == "APPROVE_FOR_PAPER_TEST"
    assert result["reports"]["boss"]["data"]["human_approval_required"] is True


def test_approval_respects_risk_limits(cfg):
    result = run_pipeline(build_context(cfg, as_of=APPROVE_DATE))
    sizing = result["reports"]["risk"]["data"]["sizing"]
    equity = cfg["risk"]["starting_equity"]
    assert sizing["max_loss"] <= equity * cfg["risk"]["max_risk_per_trade_pct"] / 100
    assert sizing["position_value"] <= equity * cfg["risk"]["max_position_pct"] / 100


def test_stale_data_stops_pipeline(cfg):
    # Decision date three weeks after the last bar -> data is stale.
    result = run_pipeline(build_context(cfg, as_of="2026-10-21"))
    assert result["stopped_at"] == "scout"
    assert result["reports"]["scout"]["status"] == "needs_data"
    assert result["decision"] == "WAIT"
    assert set(result["reports"]) == {"scout", "boss"}


def test_risk_veto_on_event(cfg):
    result = run_pipeline(build_context(cfg, as_of=EVENT_REJECT_DATE))
    assert result["stopped_at"] == "risk"
    assert result["reports"]["risk"]["status"] == "rejected"
    failed = [c["check"] for c in result["reports"]["risk"]["data"]["checks"] if not c["passed"]]
    assert "event_blackout" in failed
    assert result["decision"] == "REJECT"


def test_risk_veto_when_portfolio_full(cfg):
    full = [Position("TEST_AAA", 1, 100.0), Position("TEST_BBB", 1, 80.0), Position("TEST_CCC", 1, 40.0)]
    result = run_pipeline(build_context(cfg, as_of=APPROVE_DATE, open_positions=full))
    failed = [c["check"] for c in result["reports"]["risk"]["data"]["checks"] if not c["passed"]]
    assert "open_positions" in failed
    assert result["decision"] == "REJECT"


def test_tightening_a_limit_cannot_be_bypassed(cfg):
    cfg["risk"]["min_avg_dollar_volume"] = 10**12  # impossible liquidity bar
    result = run_pipeline(build_context(cfg, as_of=APPROVE_DATE))
    assert result["decision"] == "REJECT"


def test_missing_news_source_stops_pipeline(cfg):
    ctx = build_context(cfg, as_of=APPROVE_DATE)
    ctx.news = copy.deepcopy(ctx.news) + [{
        "id": "BAD1", "ticker": "TEST_DDD", "headline": "TEST_DDD soars on secret deal",
        "source": "", "url": "", "published_at": "2026-09-17T09:00:00Z"}]
    result = run_pipeline(ctx)
    assert result["stopped_at"] == "news"
    assert result["reports"]["news"]["status"] == "needs_data"
    assert result["decision"] == "WAIT"


def test_boss_cannot_approve_with_missing_report(cfg):
    ctx = build_context(cfg, as_of=APPROVE_DATE)
    reports = run_pipeline(ctx)["reports"]
    del reports["charts"]
    out = boss.run(ctx, reports)
    assert out["data"]["decision"] != "APPROVE_FOR_PAPER_TEST"


def test_boss_cannot_approve_with_tampered_risk_report(cfg):
    ctx = build_context(cfg, as_of=APPROVE_DATE)
    reports = run_pipeline(ctx)["reports"]
    del reports["risk"]["timestamp_utc"]  # invalid report
    out = boss.run(ctx, reports)
    assert out["data"]["decision"] != "APPROVE_FOR_PAPER_TEST"


def test_agent_crash_never_approves(cfg, monkeypatch):
    from opus_desk import pipeline

    def boom(ctx, reports):
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(pipeline, "ANALYSTS", [(n, boom if n == "charts" else f) for n, f in pipeline.ANALYSTS])
    result = run_pipeline(build_context(cfg, as_of=APPROVE_DATE))
    assert result["stopped_at"] == "charts" and result["decision"] == "WAIT"


def test_no_lookahead(cfg):
    """The decision on a date must not change when future data is removed."""
    full = run_pipeline(build_context(cfg, as_of=APPROVE_DATE))
    ctx = build_context(cfg, as_of=APPROVE_DATE)
    cut = pd.Timestamp(APPROVE_DATE)
    assert all(df.index.max() <= cut for df in ctx.prices.values())
    assert all(pd.Timestamp(n["published_at"]).tz_localize(None) <= cut + pd.Timedelta(days=1) for n in ctx.news)
    assert run_pipeline(ctx)["decision"] == full["decision"]


def test_no_live_order_code_exists():
    """There must be no brokerage or order-sending code anywhere in the package."""
    pkg = Path(__file__).resolve().parent.parent / "opus_desk"
    banned = ["place_order", "submit_order", "create_order", "send_order", "alpaca", "ib_insync",
              "ccxt", "binance", "brokerage_api"]
    for path in pkg.rglob("*.py"):
        text = path.read_text().lower()
        for word in banned:
            assert word not in text, f"{word!r} found in {path.name}"
