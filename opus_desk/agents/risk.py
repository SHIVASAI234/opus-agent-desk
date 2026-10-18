"""RISK: hard-coded gatekeeper with veto power.

Every limit comes from config.yaml and is enforced in Python. There is no prompt
to talk it round. Any failed check -> status "rejected" and the pipeline stops.
"""
from __future__ import annotations

import math

import pandas as pd

from ..schema import make_report
from . import RunContext


def run(ctx: RunContext, reports: dict) -> dict:
    lim = ctx.cfg["risk"]
    strat = reports["strategy"]["data"]
    if strat.get("action") != "PLAN":
        return make_report("risk", ctx.as_of_str, "complete", "No plan to check (Strategy returned WAIT).",
                           next_step="Boss returns WAIT.", data={"verdict": "NO_PLAN", "checks": []})

    plan = strat["plan"]
    t = plan["ticker"]
    df = ctx.prices.get(t)
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "passed": bool(ok), "detail": detail})

    # 1. Plan completeness
    required = ["ticker", "reference_price", "stop", "target", "max_holding_days", "invalidation"]
    missing = [k for k in required if plan.get(k) in (None, "")]
    check("plan_complete", not missing, f"missing: {missing}" if missing else "all fields present")
    if missing or df is None or df.empty:
        reason = f"plan missing {missing}" if missing else f"no price data for {t}"
        return make_report("risk", ctx.as_of_str, "rejected", f"REJECTED: {reason}",
                           evidence=[{"claim": reason, "source": "risk limits in config.yaml"}],
                           risks=[reason], next_step="Pipeline stops. Boss must return REJECT.",
                           data={"verdict": "REJECTED", "checks": checks})

    # 2. Data freshness
    age = (ctx.as_of - df.index[-1]).days if df is not None and not df.empty else 10**6
    check("data_fresh", age <= lim["max_data_age_days"], f"latest bar {age} day(s) old (max {lim['max_data_age_days']})")

    # 3. Valid long structure
    ref, stop, target = plan["reference_price"], plan["stop"], plan["target"]
    check("stop_below_entry", stop < ref, f"stop {stop} vs reference {ref}")
    check("target_above_entry", target > ref, f"target {target} vs reference {ref}")

    # 4. Position sizing (fixed-fractional risk, capped by position size, no leverage)
    equity = ctx.equity
    risk_per_share = ref - stop
    qty = 0
    if risk_per_share > 0:
        qty_by_risk = math.floor(equity * lim["max_risk_per_trade_pct"] / 100 / risk_per_share)
        qty_by_size = math.floor(equity * lim["max_position_pct"] / 100 / ref)
        qty = max(0, min(qty_by_risk, qty_by_size))
    position_value = qty * ref
    check("size_positive", qty >= 1, f"{qty} share(s), value {position_value:,.0f}")
    max_loss = qty * risk_per_share
    check("max_loss_within_limit", max_loss <= equity * lim["max_risk_per_trade_pct"] / 100 + 1e-6,
          f"loss at stop {max_loss:,.0f} vs limit {equity * lim['max_risk_per_trade_pct'] / 100:,.0f}")

    # 5. Portfolio exposure and position count
    open_value = sum(p.qty * float(ctx.prices[p.ticker]["close"].iloc[-1]) for p in ctx.open_positions
                     if p.ticker in ctx.prices)
    exposure_pct = (open_value + position_value) / equity * 100 if equity > 0 else float("inf")
    check("total_exposure", exposure_pct <= lim["max_total_exposure_pct"],
          f"{exposure_pct:.1f}% of equity after trade (max {lim['max_total_exposure_pct']}%)")
    check("open_positions", len(ctx.open_positions) < lim["max_open_positions"],
          f"{len(ctx.open_positions)} open (max {lim['max_open_positions']})")
    check("not_already_held", t not in {p.ticker for p in ctx.open_positions}, f"{t} not currently held")

    # 6. Liquidity
    adv = float((df["close"] * df["volume"]).iloc[-20:].mean()) if df is not None else 0.0
    check("liquidity", adv >= lim["min_avg_dollar_volume"],
          f"20d avg traded value {adv:,.0f} (min {lim['min_avg_dollar_volume']:,.0f})")

    # 7. Correlation with open positions
    worst = None
    if ctx.open_positions:
        rets = df["close"].pct_change().iloc[-60:]
        for p in ctx.open_positions:
            other = ctx.prices[p.ticker]["close"].pct_change().iloc[-60:]
            corr = rets.corr(other)
            if pd.notna(corr) and (worst is None or corr > worst[1]):
                worst = (p.ticker, float(corr))
    check("correlation", worst is None or worst[1] <= lim["max_correlation"],
          "no open positions" if worst is None else f"max corr {worst[1]:.2f} with {worst[0]} (max {lim['max_correlation']})")

    # 8. Event risk
    events = reports["news"]["data"].get("by_ticker", {}).get(t, {}).get("upcoming_events", [])
    hold_end = ctx.as_of + pd.tseries.offsets.BDay(plan["max_holding_days"] + 1)
    in_window = [e for e in events if pd.Timestamp(e["event_date"]) <= hold_end]
    check("event_blackout", not (lim["event_blackout"] and in_window),
          f"events in holding window: {[e['event_type'] + ' ' + e['event_date'] for e in in_window]}"
          if in_window else "no scheduled events in holding window")

    failed = [c for c in checks if not c["passed"]]
    data = {"verdict": "REJECTED" if failed else "PASSED", "checks": checks,
            "sizing": {"qty": qty, "position_value": round(position_value, 2),
                       "risk_per_share": round(risk_per_share, 4), "max_loss": round(max_loss, 2),
                       "equity": round(equity, 2)}}
    evidence = [{"claim": f"{c['check']}: {'PASS' if c['passed'] else 'FAIL'} ({c['detail']})",
                 "source": "risk limits in config.yaml"} for c in checks]
    if failed:
        return make_report("risk", ctx.as_of_str, "rejected",
                           f"REJECTED: {', '.join(c['check'] for c in failed)}",
                           evidence=evidence, risks=[f"{c['check']}: {c['detail']}" for c in failed],
                           next_step="Pipeline stops. Boss must return REJECT.", data=data)
    return make_report("risk", ctx.as_of_str, "complete",
                       f"PASSED: {qty} share(s) of {t}, max loss {max_loss:,.0f}",
                       evidence=evidence, next_step="Boss makes the final call.", data=data)
