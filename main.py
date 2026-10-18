"""Opus Agent Desk - command line entry point.

    python main.py                         # run the six agents on the latest fixture date
    python main.py run --as-of 2026-06-15  # run as of a specific date
    python main.py backtest                # walk-forward backtest over the whole history
    python main.py --mode fixture          # same as `run` (fixture data is the only mode)
"""
from __future__ import annotations

import argparse
import sys

from opus_desk.config import SafetyError, load_config
from opus_desk.dashboard import write_dashboard
from opus_desk.pipeline import build_context, run_pipeline, save_run


def _llm_client(cfg):
    if not cfg["news"].get("use_llm"):
        return None
    from opus_desk.llm import ClaudeClient
    return ClaudeClient(cfg["llm"]["model"], cfg["llm"]["max_tokens"])


def cmd_run(cfg, as_of: str | None) -> int:
    ctx = build_context(cfg, as_of=as_of, llm_client=_llm_client(cfg))
    result = run_pipeline(ctx)
    path = save_run(result)
    dash = write_dashboard(result)
    print(f"Data: {result['data_label']}  |  as of {result['data_as_of']}  |  mode: {result['mode']}")
    print("-" * 72)
    for name, rep in result["reports"].items():
        print(f"{name.upper():9} [{rep['status']:10}] {rep['summary']}")
    for name in ("scout", "news", "charts", "strategy", "risk"):
        if name not in result["reports"]:
            print(f"{name.upper():9} [skipped   ] pipeline stopped at {result['stopped_at']}")
    print("-" * 72)
    print(f"FINAL DECISION: {result['decision']}")
    print(f"Report:    {path.relative_to(path.parent.parent)}")
    print(f"Dashboard: {dash.relative_to(dash.parent.parent)}")
    return 0


def cmd_backtest(cfg) -> int:
    from opus_desk.backtest import run_backtest
    print("Running walk-forward backtest (this takes a few seconds)...")
    s = run_backtest(cfg)
    for k, v in s.items():
        print(f"{k:38} {v}")
    print("Saved: reports/backtest_summary.json, backtest_trades.csv, backtest_equity.csv")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Opus Agent Desk (paper-trading research only)")
    p.add_argument("command", nargs="?", default="run", choices=["run", "backtest"])
    p.add_argument("--as-of", help="decision date YYYY-MM-DD (default: latest date in data)")
    p.add_argument("--mode", default="fixture", choices=["fixture"],
                   help="data source; only local fixture data is supported")
    p.add_argument("--config", help="path to config.yaml")
    args = p.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except SafetyError as exc:
        print(f"SAFETY STOP: {exc}", file=sys.stderr)
        return 2
    if args.command == "backtest":
        return cmd_backtest(cfg)
    return cmd_run(cfg, args.as_of)


if __name__ == "__main__":
    sys.exit(main())
