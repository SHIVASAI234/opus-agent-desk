"""Download real daily prices, news and earnings dates into data/real/.

Keys come from environment variables (Codespaces secrets or a local .env):
    POLYGON_API_KEY  TWELVEDATA_API_KEY  FINNHUB_API_KEY  ALPHAVANTAGE_API_KEY
    NEWSAPI_API_KEY  MARKETAUX_API_KEY
Any provider without a key is skipped.

    python scripts/fetch_data.py                 # prices + news + earnings
    python scripts/fetch_data.py --reconcile     # also cross-check prices between providers
    python scripts/fetch_data.py --skip-news     # prices and earnings only
    python scripts/fetch_data.py --check-keys    # show which keys are set (never prints them)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from opus_desk.config import ROOT, load_config  # noqa: E402
from opus_desk.providers import KEY_ENV, ProviderError, Throttle  # noqa: E402
from opus_desk.providers.market import FETCHERS, fetch_with_fallback, quality_checks, reconcile  # noqa: E402
from opus_desk.providers.news import EARNINGS_FETCHERS, NEWS_FETCHERS, dedupe  # noqa: E402


def available_keys() -> dict[str, str]:
    return {p: os.environ[env] for p, env in KEY_ENV.items() if os.environ.get(env)}


def main(argv: list[str] | None = None, session=None) -> int:
    ap = argparse.ArgumentParser(description="Fetch real market data, news and earnings dates")
    ap.add_argument("--config", default=str(ROOT / "config_real.yaml"))
    ap.add_argument("--reconcile", action="store_true", help="fetch from every market provider and compare")
    ap.add_argument("--skip-news", action="store_true")
    ap.add_argument("--check-keys", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    f = cfg["fetch"]
    keys = available_keys()

    print("API keys found:")
    for p, env in KEY_ENV.items():
        print(f"  {env:24} {'set' if p in keys else '-'}")
    if args.check_keys:
        return 0
    if not any(p in keys for p in f["market_providers"]):
        print("\nNo market-data key set. Add at least one (see README), then re-run.")
        return 1

    session = session or requests.Session()
    throttle = Throttle(f["min_seconds_between_calls"], f["daily_call_budget"])
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=int(365.25 * f["history_years"]))

    prices_dir = ROOT / cfg["data"]["prices_dir"]
    prices_dir.mkdir(parents=True, exist_ok=True)
    reports_dir = ROOT / cfg.get("reports_dir", "reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    manifest = {"fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "requested_range": [start.isoformat(), end.isoformat()], "prices": {}, "news": {},
                "earnings": {}, "failed_tickers": [], "assumptions": [
                    f"Earnings dates treated as public {f['earnings_known_days_before']} days before the event.",
                    "Polygon prices requested split-adjusted; other providers use their defaults."]}

    # ---------- 1. Prices ----------
    print(f"\nPrices {start} to {end} (this can take a few minutes on free tiers)...")
    recon_frames: dict[str, dict[str, pd.DataFrame]] = {}
    for t in cfg["universe"]:
        df, used, errors = fetch_with_fallback(session, t, start, end, f["market_providers"], keys, throttle)
        if df is None:
            print(f"  {t:6} FAILED: {' | '.join(errors) or 'no provider available'}")
            manifest["failed_tickers"].append({"ticker": t, "errors": errors})
            continue
        issues = quality_checks(df)
        df.to_csv(prices_dir / f"{t}.csv", index=False)
        manifest["prices"][t] = {"provider": used, "rows": len(df), "first": df["date"].iloc[0],
                                 "last": df["date"].iloc[-1], "quality_issues": issues, "fallback_errors": errors}
        flag = f"  ! {'; '.join(issues)}" if issues else ""
        print(f"  {t:6} {used:12} {len(df):4} rows {df['date'].iloc[0]} to {df['date'].iloc[-1]}{flag}")
        if args.reconcile:
            recon_frames[t] = {used: df}
            for p in f["market_providers"]:
                if p == used or p not in keys:
                    continue
                try:
                    recon_frames[t][p] = FETCHERS[p](session, t, start, end, keys[p], throttle)
                except ProviderError as exc:
                    print(f"         reconcile: {exc}")

    # ---------- 2. Reconciliation ----------
    if args.reconcile:
        all_breaks, all_summary = [], []
        for t, frames in recon_frames.items():
            if len(frames) < 2:
                continue
            breaks, summary = reconcile(frames, f["reconcile_tolerance_pct"])
            if not breaks.empty:
                breaks.insert(0, "ticker", t)
                all_breaks.append(breaks)
            all_summary += [{"ticker": t, **s} for s in summary]
        if all_summary:
            pd.DataFrame(all_summary).to_csv(reports_dir / "data_reconciliation_summary.csv", index=False)
            (pd.concat(all_breaks) if all_breaks else pd.DataFrame(
                columns=["ticker", "date", "provider_a", "close_a", "provider_b", "close_b", "diff_pct"])
             ).to_csv(reports_dir / "data_reconciliation_breaks.csv", index=False)
            print(f"\nReconciliation: {sum(s['breaks'] for s in all_summary)} break(s) above "
                  f"{f['reconcile_tolerance_pct']}% across {len(all_summary)} provider pair(s). "
                  f"See {reports_dir.relative_to(ROOT)}/data_reconciliation_*.csv")
        else:
            print("\nReconciliation skipped: need keys for at least two market providers.")

    # ---------- 3. Earnings dates ----------
    items: list[dict] = []
    ok_tickers = list(manifest["prices"])
    ev_end = end + timedelta(days=90)  # include upcoming dates for live runs
    for t in ok_tickers:
        for p in f["earnings_providers"]:
            if p not in keys:
                continue
            try:
                evs = EARNINGS_FETCHERS[p](session, t, start, ev_end, keys[p],
                                           f["earnings_known_days_before"], throttle)
                items += evs
                manifest["earnings"][t] = {"provider": p, "events": len(evs)}
                break
            except ProviderError as exc:
                manifest["earnings"].setdefault(t, {"errors": []}).setdefault("errors", []).append(str(exc))

    # ---------- 4. News ----------
    if not args.skip_news:
        news_start = end - timedelta(days=f["news_days"])
        print(f"\nNews {news_start} to {end}...")
        for p in f["news_providers"]:
            if p not in keys:
                continue
            got, errs = 0, []
            for t in ok_tickers:
                query = f["company_names"].get(t, t)
                try:
                    batch = NEWS_FETCHERS[p](session, t, query, news_start, end, keys[p], throttle)
                    items += batch
                    got += len(batch)
                except ProviderError as exc:
                    errs.append(f"{t}: {exc}")
            manifest["news"][p] = {"items": got, "errors": errs[:10]}
            print(f"  {p:12} {got:5} item(s){f'  ({len(errs)} error(s))' if errs else ''}")

    news_items = [i for i in items if i.get("headline") and i.get("url") and i.get("source")]
    news_items, dropped = dedupe(news_items)
    news_path = ROOT / cfg["data"]["news_file"]
    news_path.parent.mkdir(parents=True, exist_ok=True)
    news_path.write_text(json.dumps(news_items, indent=1))
    manifest["news_total_after_dedupe"] = len(news_items)
    manifest["news_duplicates_removed"] = dropped
    dated = [i["published_at"] for i in news_items if not i.get("event_type")]
    manifest["news_coverage"] = [min(dated)[:10], max(dated)[:10]] if dated else None
    manifest["provider_calls"] = throttle.calls

    (ROOT / cfg["data"]["prices_dir"]).parent.joinpath("fetch_manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"\nSaved {len(ok_tickers)} price file(s), {len(news_items)} news/event item(s) "
          f"({dropped} duplicate(s) removed). Manifest: data/real/fetch_manifest.json")
    if manifest["failed_tickers"]:
        failed = [x["ticker"] for x in manifest["failed_tickers"]]
        print(f"\nACTION NEEDED: remove {', '.join(failed)} from `universe` in {Path(args.config).name} "
              "(the Scout stops if any ticker has no data), or fix the provider error above.")
        return 3
    print("\nNext: python main.py backtest --config config_real.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
