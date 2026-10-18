"""STRATEGY: turn research into one hypothetical long-only paper-trade plan, or WAIT.

The rules are deliberately simple and written down, so they can be backtested.
Rule: uptrend + breakout above the prior 20-day high + RSI not overheated
      + no conflicting chart signal + news not negative.
"""
from __future__ import annotations

from ..schema import make_report
from . import RunContext


def run(ctx: RunContext, reports: dict) -> dict:
    cfg = ctx.cfg["strategy"]
    watchlist = reports["scout"]["data"].get("watchlist", [])
    charts = reports["charts"]["data"].get("by_ticker", {})
    news = reports["news"]["data"].get("by_ticker", {})

    considered, eligible = [], []
    for w in watchlist:
        t = w["ticker"]
        c, n = charts.get(t), news.get(t, {"sentiment": "neutral"})
        reasons = []
        if c is None:
            reasons.append("no chart report")
        else:
            if c["trend"] != "up":
                reasons.append(f"trend is {c['trend']}, not up")
            if not c["breakout"]:
                reasons.append("no breakout above prior 20-day high")
            if c["rsi"] > cfg["rsi_max"]:
                reasons.append(f"RSI {c['rsi']} above {cfg['rsi_max']}")
            if c["conflicts"]:
                reasons.append("chart conflicts: " + "; ".join(c["conflicts"]))
        if n["sentiment"] == "negative":
            reasons.append("news sentiment negative")
        considered.append({"ticker": t, "eligible": not reasons, "reasons": reasons or ["all rules met"]})
        if not reasons:
            eligible.append((w["score"], t))

    if not eligible:
        return make_report(
            "strategy", ctx.as_of_str, "complete",
            "WAIT: no watchlist ticker meets every rule." if watchlist else "WAIT: empty watchlist.",
            evidence=[{"claim": f"{x['ticker']}: {'; '.join(x['reasons'])}", "source": "scout/news/charts reports"}
                      for x in considered],
            next_step="Risk has nothing to check; Boss should return WAIT.",
            data={"action": "WAIT", "considered": considered},
        )

    eligible.sort(reverse=True)
    t = eligible[0][1]
    c = charts[t]
    ref = c["close"]
    plan = {
        "ticker": t,
        "side": "long",
        "entry_condition": "Buy at next session's open (hypothetical, paper only).",
        "reference_price": ref,
        "stop": round(ref - cfg["stop_atr_multiple"] * c["atr"], 4),
        "target": round(ref + cfg["target_atr_multiple"] * c["atr"], 4),
        "max_holding_days": cfg["max_holding_days"],
        "invalidation": f"Close below stop ({cfg['stop_atr_multiple']}x ATR under reference price).",
        "target_logic": f"{cfg['target_atr_multiple']}x ATR above reference price "
                        f"({cfg['target_atr_multiple'] / cfg['stop_atr_multiple']:.1f}R).",
    }
    return make_report(
        "strategy", ctx.as_of_str, "complete",
        f"PLAN: long {t} next open, stop {plan['stop']}, target {plan['target']}, "
        f"max {plan['max_holding_days']} days.",
        evidence=[{"claim": f"{x['ticker']}: {'; '.join(x['reasons'])}", "source": "scout/news/charts reports"}
                  for x in considered],
        risks=["Rule-based plan; no claim of positive expectancy until backtested."],
        next_step="Risk checks sizing, exposure, liquidity, correlation and events.",
        data={"action": "PLAN", "plan": plan, "considered": considered},
    )
