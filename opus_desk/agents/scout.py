"""SCOUT: find tickers with unusual activity. Builds a watchlist, never a trade idea.

Deterministic: z-scores of volume and daily return, plus a 20-day breakout flag.
"""
from __future__ import annotations

from ..indicators import zscore_last
from ..schema import make_report
from . import RunContext


def run(ctx: RunContext, reports: dict) -> dict:
    cfg = ctx.cfg["scout"]
    max_age = ctx.cfg["risk"]["max_data_age_days"]
    lookback = cfg["lookback_days"]
    src = f"price data ({ctx.data_label})"

    missing, stale, too_short, candidates = [], [], [], []
    for ticker in ctx.cfg["universe"]:
        df = ctx.prices.get(ticker)
        if df is None or df.empty:
            missing.append(ticker)
            continue
        age = (ctx.as_of - df.index[-1]).days
        if age > max_age:
            stale.append(f"{ticker} (last bar {df.index[-1].date()}, {age}d old)")
            continue
        if len(df) < lookback + 1:
            too_short.append(ticker)
            continue

        rets = df["close"].pct_change()
        vol_z = zscore_last(df["volume"], lookback)
        ret_z = zscore_last(rets.dropna(), lookback)
        prior_high = df["high"].iloc[-(lookback + 1):-1].max()
        breakout = bool(df["close"].iloc[-1] > prior_high)

        reasons = []
        if vol_z is not None and vol_z >= cfg["volume_z_threshold"]:
            reasons.append(f"volume z={vol_z:.2f}")
        if ret_z is not None and abs(ret_z) >= cfg["return_z_threshold"]:
            reasons.append(f"daily move z={ret_z:.2f}")
        if breakout:
            reasons.append(f"close above prior {lookback}-day high")
        if reasons:
            score = max(abs(vol_z or 0), abs(ret_z or 0)) + (1.0 if breakout else 0.0)
            candidates.append({
                "ticker": ticker,
                "score": round(score, 3),
                "volume_z": None if vol_z is None else round(vol_z, 3),
                "return_z": None if ret_z is None else round(ret_z, 3),
                "breakout": breakout,
                "reasons": reasons,
            })

    if missing or stale:
        problems = []
        if missing:
            problems.append(f"no data for {', '.join(missing)}")
        if stale:
            problems.append(f"stale data: {'; '.join(stale)}")
        return make_report(
            "scout", ctx.as_of_str, "needs_data",
            "Cannot scan: " + " | ".join(problems),
            risks=problems,
            next_step="Refresh market data before re-running.",
            data={"missing": missing, "stale": stale},
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    watchlist = candidates[: cfg["max_watchlist"]]
    evidence = [
        {"claim": f"{c['ticker']}: {', '.join(c['reasons'])}", "source": src, "value": c["score"]}
        for c in watchlist
    ]
    summary = (
        f"{len(watchlist)} ticker(s) flagged for review: {', '.join(c['ticker'] for c in watchlist)}"
        if watchlist else "No unusual activity found in the universe."
    )
    risks = [f"Insufficient history for {', '.join(too_short)}"] if too_short else []
    return make_report(
        "scout", ctx.as_of_str, "complete", summary,
        evidence=evidence, risks=risks,
        next_step="News and Charts review the watchlist." if watchlist else "Nothing to review.",
        data={"watchlist": watchlist},
    )
