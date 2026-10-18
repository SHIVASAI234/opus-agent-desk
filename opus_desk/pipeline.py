"""Run Scout -> News -> Charts -> Strategy -> Risk in order, then Boss.

If any agent returns needs_data or rejected (or produces an invalid report),
the remaining analysis agents are skipped and Boss decides on what exists,
which can never be an approval.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

from .agents import Position, RunContext, boss, charts, news, risk, scout, strategy
from .config import ROOT
from .data import load_news, load_prices, news_as_of, slice_as_of
from .schema import ReportValidationError, make_report, utc_now, validate_report

ANALYSTS = [("scout", scout.run), ("news", news.run), ("charts", charts.run),
            ("strategy", strategy.run), ("risk", risk.run)]


def run_pipeline(ctx: RunContext) -> dict[str, Any]:
    reports: dict[str, dict] = {}
    stopped_at = None
    for name, fn in ANALYSTS:
        try:
            rep = fn(ctx, reports)
            validate_report(rep)
        except ReportValidationError as exc:
            rep = make_report(name, ctx.as_of_str, "needs_data", f"Invalid report: {exc}",
                              next_step="Pipeline stopped on schema failure.")
        except Exception as exc:  # an agent crash must never turn into an approval
            rep = make_report(name, ctx.as_of_str, "needs_data", f"Agent error: {type(exc).__name__}: {exc}",
                              next_step="Pipeline stopped on agent error.")
        reports[name] = rep
        if rep["status"] != "complete":
            stopped_at = name
            break
    reports["boss"] = boss.run(ctx, reports)
    validate_report(reports["boss"])
    return {
        "run_timestamp_utc": utc_now(),
        "data_as_of": ctx.as_of_str,
        "data_label": ctx.data_label,
        "mode": ctx.cfg["mode"],
        "allow_live_orders": ctx.cfg["allow_live_orders"],
        "stopped_at": stopped_at,
        "decision": reports["boss"]["data"]["decision"],
        "reports": reports,
    }


def build_context(cfg: dict, as_of: str | None = None, equity: float | None = None,
                  open_positions: list[Position] | None = None, llm_client=None) -> RunContext:
    prices = load_prices(cfg["data"]["prices_dir"], cfg["universe"])
    all_news = load_news(cfg["data"]["news_file"])
    if as_of is None:
        as_of_ts = max(df.index[-1] for df in prices.values())
    else:
        as_of_ts = pd.Timestamp(as_of)
    return RunContext(
        as_of=as_of_ts,
        prices=slice_as_of(prices, as_of_ts),
        news=news_as_of(all_news, as_of_ts),
        cfg=cfg,
        equity=equity if equity is not None else cfg["risk"]["starting_equity"],
        open_positions=open_positions or [],
        llm_client=llm_client,
    )


def save_run(result: dict[str, Any], reports_dir: Path | None = None) -> Path:
    reports_dir = reports_dir or ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = result["run_timestamp_utc"].replace(":", "").replace("-", "")
    path = reports_dir / f"run_{result['data_as_of']}_{stamp}.json"
    path.write_text(json.dumps(result, indent=2))

    # Append-only audit log: one row per run.
    log = reports_dir / "decision_log.csv"
    new = not log.exists()
    with open(log, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["run_timestamp_utc", "data_as_of", "data_label", "decision", "stopped_at",
                        "ticker", "stop", "target", "qty", "report_file"])
        plan = result["reports"]["boss"]["data"].get("plan") or {}
        sizing = result["reports"]["boss"]["data"].get("sizing") or {}
        w.writerow([result["run_timestamp_utc"], result["data_as_of"], result["data_label"],
                    result["decision"], result["stopped_at"] or "", plan.get("ticker", ""),
                    plan.get("stop", ""), plan.get("target", ""), sizing.get("qty", ""), path.name])
    return path
