"""Walk-forward backtest of the full six-agent pipeline.

At each date the pipeline only sees data up to that date. An approved plan is
filled at the NEXT day's open, with commission and slippage on both sides.
Results are compared with an equal-weight buy-and-hold benchmark.

Conservative assumptions:
- If a bar touches both stop and target, we assume the stop hit first.
- If the price gaps below the stop at the open, we exit at the open (worse than the stop).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd

from .agents import Position, RunContext
from .config import ROOT
from .data import load_news, load_prices, news_as_of, slice_as_of
from .pipeline import run_pipeline


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float(((equity - peak) / peak).min())


def run_backtest(cfg: dict, out_dir: Path | None = None) -> dict[str, Any]:
    bt = cfg["backtest"]
    slip = bt["slippage_bps"] / 1e4
    comm = bt["commission_bps"] / 1e4
    max_hold = cfg["strategy"]["max_holding_days"]

    prices = load_prices(cfg["data"]["prices_dir"], cfg["universe"])
    all_news = load_news(cfg["data"]["news_file"])
    dates = sorted(set().union(*[df.index for df in prices.values()]))
    warmup = bt["warmup_days"]
    if len(dates) <= warmup + 1:
        raise ValueError("Not enough history for the warm-up period.")

    cash = float(cfg["risk"]["starting_equity"])
    positions: dict[str, dict] = {}
    pending: dict | None = None
    trades, curve, decisions = [], [], Counter()
    skipped_entries = 0

    for i in range(warmup, len(dates)):
        d = dates[i]

        # 1. Fill yesterday's approved plan at today's open.
        if pending is not None:
            t = pending["ticker"]
            df = prices[t]
            if d in df.index and t not in positions:
                bar = df.loc[d]
                fill = bar["open"] * (1 + slip)
                qty = pending["qty"]
                cost = qty * fill * (1 + comm)
                if bar["open"] <= pending["stop"] or cost > cash or qty < 1:
                    skipped_entries += 1  # gapped through stop, or not enough cash
                else:
                    cash -= cost
                    positions[t] = {"qty": qty, "entry_fill": fill, "entry_date": d,
                                    "stop": pending["stop"], "target": pending["target"],
                                    "initial_risk": qty * (fill - pending["stop"]), "days": 0,
                                    "entry_cost": cost}
            pending = None

        # 2. Manage open positions on today's bar.
        for t in list(positions):
            df = prices[t]
            if d not in df.index:
                continue
            pos, bar = positions[t], df.loc[d]
            pos["days"] += 1
            exit_px, reason = None, None
            if bar["open"] <= pos["stop"]:
                exit_px, reason = bar["open"], "gap_stop"
            elif bar["low"] <= pos["stop"]:
                exit_px, reason = pos["stop"], "stop"
            elif bar["high"] >= pos["target"]:
                exit_px, reason = max(bar["open"], pos["target"]), "target"
            elif pos["days"] >= max_hold:
                exit_px, reason = bar["close"], "time"
            if exit_px is not None:
                fill = exit_px * (1 - slip)
                proceeds = pos["qty"] * fill * (1 - comm)
                cash += proceeds
                pnl = proceeds - pos["entry_cost"]
                trades.append({
                    "ticker": t, "entry_date": pos["entry_date"].date(), "exit_date": d.date(),
                    "qty": pos["qty"], "entry_fill": round(pos["entry_fill"], 4),
                    "exit_fill": round(fill, 4), "exit_reason": reason, "days_held": pos["days"],
                    "pnl": round(pnl, 2),
                    "r_multiple": round(pnl / pos["initial_risk"], 3) if pos["initial_risk"] > 0 else None,
                })
                del positions[t]

        # 3. Mark to market.
        mv = sum(p["qty"] * float(prices[t]["close"].loc[:d].iloc[-1]) for t, p in positions.items())
        equity = cash + mv
        curve.append({"date": d.date(), "equity": round(equity, 2), "cash": round(cash, 2),
                      "invested_pct": round(mv / equity * 100, 2) if equity else 0.0})

        # 4. Run the pipeline on data up to today (never beyond).
        if i == len(dates) - 1:
            break
        ctx = RunContext(
            as_of=d, prices=slice_as_of(prices, d), news=news_as_of(all_news, d), cfg=cfg,
            equity=equity,
            open_positions=[Position(t, p["qty"], p["entry_fill"]) for t, p in positions.items()],
        )
        result = run_pipeline(ctx)
        decisions[result["decision"]] += 1
        if result["decision"] == "APPROVE_FOR_PAPER_TEST":
            boss = result["reports"]["boss"]["data"]
            pending = {"ticker": boss["plan"]["ticker"], "stop": boss["plan"]["stop"],
                       "target": boss["plan"]["target"], "qty": boss["sizing"]["qty"]}

    eq = pd.Series([c["equity"] for c in curve], index=[c["date"] for c in curve])
    start_equity = float(cfg["risk"]["starting_equity"])

    # Benchmark: equal-weight buy-and-hold from the first tradable open to the last close.
    start_d, end_d = dates[warmup], dates[-1]
    bench_rets = []
    for df in prices.values():
        buy = df.loc[start_d:, "open"].iloc[0] * (1 + slip) * (1 + comm)
        sell = df.loc[:end_d, "close"].iloc[-1] * (1 - slip) * (1 - comm)
        bench_rets.append(sell / buy - 1)
    bench_return = sum(bench_rets) / len(bench_rets)

    tdf = pd.DataFrame(trades)
    n = len(tdf)
    summary = {
        "data_label": cfg["data"]["label"],
        "period": f"{start_d.date()} to {end_d.date()}",
        "trading_days": len(curve),
        "starting_equity": start_equity,
        "ending_equity": round(float(eq.iloc[-1]), 2),
        "total_return_pct": round((float(eq.iloc[-1]) / start_equity - 1) * 100, 2),
        "benchmark_equal_weight_buy_hold_pct": round(bench_return * 100, 2),
        "max_drawdown_pct": round(_max_drawdown(eq) * 100, 2),
        "trades": n,
        "win_rate_pct": round(float((tdf["pnl"] > 0).mean() * 100), 1) if n else None,
        "avg_r_multiple": round(float(tdf["r_multiple"].mean()), 3) if n else None,
        "exit_reasons": dict(Counter(tdf["exit_reason"])) if n else {},
        "avg_invested_pct": round(float(pd.Series([c["invested_pct"] for c in curve]).mean()), 2),
        "decisions": dict(decisions),
        "approval_rate_pct": round(decisions["APPROVE_FOR_PAPER_TEST"] / max(1, sum(decisions.values())) * 100, 2),
        "skipped_entries": skipped_entries,
        "costs": f"{bt['commission_bps']}bps commission + {bt['slippage_bps']}bps slippage per side",
        "warnings": [],
    }
    if n < 30:
        summary["warnings"].append(f"Only {n} trades: far too few to conclude anything about edge.")
    if "synthetic" in cfg["data"]["label"].lower() or "test" in cfg["data"]["label"].lower():
        summary["warnings"].append("Synthetic TEST DATA: results say nothing about real markets.")
    headlines = sorted(n["published_at"] for n in all_news if n.get("published_at") and not n.get("event_type"))
    if not headlines:
        summary["warnings"].append("No news headlines loaded: news checks were inactive for the whole test.")
    elif pd.Timestamp(headlines[0]).tz_localize(None) > start_d:
        summary["warnings"].append(f"News only covers {headlines[0][:10]} onwards; earlier decisions ran without news.")
    summary["warnings"] += list(cfg["data"].get("warnings", []))

    out_dir = out_dir or ROOT / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "backtest_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    tdf.to_csv(out_dir / "backtest_trades.csv", index=False)
    pd.DataFrame(curve).to_csv(out_dir / "backtest_equity.csv", index=False)
    return summary
