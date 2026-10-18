"""CHARTS: describe trend, momentum, levels and volume. Deterministic indicators only."""
from __future__ import annotations

from ..indicators import atr, rsi, sma
from ..schema import make_report
from . import RunContext


def run(ctx: RunContext, reports: dict) -> dict:
    cfg = ctx.cfg["charts"]
    watchlist = [w["ticker"] for w in reports["scout"]["data"].get("watchlist", [])]
    if not watchlist:
        return make_report("charts", ctx.as_of_str, "complete", "No watchlist, so no charts to review.",
                           next_step="Nothing to review.", data={"by_ticker": {}})

    short = [t for t in watchlist if len(ctx.prices[t]) < cfg["min_history_days"]]
    if short:
        return make_report(
            "charts", ctx.as_of_str, "needs_data",
            f"Need at least {cfg['min_history_days']} bars for: {', '.join(short)}",
            next_step="Load a longer history window.", data={"short_history": short},
        )

    by_ticker, evidence, risks = {}, [], []
    src = f"OHLCV ({ctx.data_label}), daily bars to {ctx.as_of_str}"
    for t in watchlist:
        df = ctx.prices[t]
        close = df["close"]
        last = float(close.iloc[-1])
        fast = float(sma(close, cfg["sma_fast"]).iloc[-1])
        slow = float(sma(close, cfg["sma_slow"]).iloc[-1])
        r = float(rsi(close, cfg["rsi_period"]).iloc[-1])
        a = float(atr(df, cfg["atr_period"]).iloc[-1])
        window = df.iloc[-(cfg["sma_fast"] + 1):-1]  # prior 20 bars, excluding today
        resistance = float(window["high"].max())
        support = float(window["low"].min())
        vol_ratio = float(df["volume"].iloc[-1] / df["volume"].iloc[-21:-1].mean())

        if last > slow and fast > slow:
            trend = "up"
        elif last < slow and fast < slow:
            trend = "down"
        else:
            trend = "mixed"

        conflicts = []
        if trend == "up" and r > 70:
            conflicts.append("uptrend but RSI overbought (>70)")
        if trend == "down" and r < 30:
            conflicts.append("downtrend but RSI oversold (<30)")
        if last > resistance and vol_ratio < 1.0:
            conflicts.append("breakout on below-average volume")
        if trend == "mixed":
            conflicts.append("price and moving averages disagree on trend")

        by_ticker[t] = {
            "close": round(last, 4), "sma_fast": round(fast, 4), "sma_slow": round(slow, 4),
            "rsi": round(r, 2), "atr": round(a, 4), "support": round(support, 4),
            "resistance": round(resistance, 4), "volume_vs_20d_avg": round(vol_ratio, 2),
            "trend": trend, "breakout": last > resistance, "conflicts": conflicts,
        }
        evidence.append({
            "claim": f"{t}: trend {trend}, close {last:.2f} vs SMA{cfg['sma_slow']} {slow:.2f}, "
                     f"RSI {r:.1f}, ATR {a:.2f}, range {support:.2f}-{resistance:.2f}",
            "source": src,
        })
        risks += [f"{t}: {c}" for c in conflicts]

    summary = "; ".join(f"{t}: {v['trend']}{' (breakout)' if v['breakout'] else ''}" for t, v in by_ticker.items())
    return make_report(
        "charts", ctx.as_of_str, "complete", summary, evidence=evidence, risks=risks,
        next_step="Strategy combines Scout, News and Charts.", data={"by_ticker": by_ticker},
    )
