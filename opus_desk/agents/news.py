"""NEWS: summarise news for watchlist tickers. Never creates a trade signal.

This is the one stage where an LLM can genuinely help (reading unstructured text).
By default it uses a simple keyword classifier so the project runs with no API key.
Hard rules stay in code: every item needs a source, URL and timestamp, stale items
are dropped, duplicates are flagged, and scheduled events are surfaced for Risk.
"""
from __future__ import annotations

import pandas as pd

from ..llm import classify_news
from ..schema import make_report
from . import RunContext

REQUIRED_FIELDS = ["id", "ticker", "headline", "source", "url", "published_at"]

POSITIVE = {"beats", "beat", "raises", "upgrade", "upgraded", "record", "approval", "approved",
            "wins", "partnership", "growth", "surge", "expands", "buyback"}
NEGATIVE = {"misses", "miss", "cuts", "downgrade", "downgraded", "probe", "investigation",
            "recall", "lawsuit", "warning", "warns", "plunge", "delay", "delayed", "fraud", "resigns"}


def _keyword_sentiment(headline: str) -> str:
    words = {w.strip(".,:;!?'\"()").lower() for w in headline.split()}
    pos, neg = len(words & POSITIVE), len(words & NEGATIVE)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def run(ctx: RunContext, reports: dict) -> dict:
    cfg = ctx.cfg["news"]
    watchlist = [w["ticker"] for w in reports["scout"]["data"].get("watchlist", [])]
    if not watchlist:
        return make_report("news", ctx.as_of_str, "complete", "No watchlist, so no news to review.",
                           next_step="Nothing to review.", data={"by_ticker": {}})

    relevant = [i for i in ctx.news if i.get("ticker") in watchlist]

    # 1. Every item must be attributable. Missing fields stop the pipeline.
    incomplete = [i.get("id", "<no id>") for i in relevant
                  if any(not i.get(f) for f in REQUIRED_FIELDS)]
    if incomplete:
        return make_report(
            "news", ctx.as_of_str, "needs_data",
            f"{len(incomplete)} news item(s) missing source, URL or timestamp: {', '.join(incomplete)}",
            risks=["Unattributed news cannot be used as evidence."],
            next_step="Fix or remove the incomplete news items.",
            data={"incomplete_items": incomplete},
        )

    cutoff = ctx.as_of - pd.Timedelta(days=cfg["lookback_days"])
    fresh, stale, seen, duplicates = [], [], set(), []
    for item in relevant:
        published = pd.Timestamp(item["published_at"]).tz_localize(None)
        if published < cutoff:
            stale.append(item["id"])
            continue
        key = (item["ticker"], item["headline"].strip().lower())
        if key in seen:
            duplicates.append(item["id"])
            continue
        seen.add(key)
        fresh.append(item)

    # 2. Classify (LLM if enabled, otherwise keywords). Kind comes from the feed if present.
    method = "keyword rules"
    if cfg.get("use_llm") and fresh:
        if ctx.llm_client is None:
            return make_report("news", ctx.as_of_str, "needs_data",
                               "use_llm is true but no LLM client is configured.",
                               next_step="Set ANTHROPIC_API_KEY or turn use_llm off.")
        classes, err = classify_news(ctx.llm_client, fresh)
        if classes is None:
            return make_report("news", ctx.as_of_str, "needs_data",
                               f"LLM output failed validation twice: {err}",
                               risks=["News classification unavailable."],
                               next_step="Inspect the LLM output; pipeline stopped.")
        by_id = {c["id"]: c for c in classes}
        method = f"LLM ({ctx.cfg['llm']['model']})"
    else:
        # Prefer the provider's own sentiment label when the feed supplies one.
        by_id = {i["id"]: {"sentiment": i.get("provider_sentiment") or _keyword_sentiment(i["headline"]),
                           "kind": i.get("kind", "unclassified")} for i in fresh}
        if any(i.get("provider_sentiment") for i in fresh):
            method = "provider sentiment labels + keyword rules"

    # 3. All known upcoming scheduled events (earnings etc.). Risk decides which
    #    ones fall inside the holding window, so News passes on every future one.
    events = []
    for item in relevant:
        if item.get("event_date"):
            ev = pd.Timestamp(item["event_date"])
            if ev > ctx.as_of:
                events.append({"ticker": item["ticker"], "event_type": item.get("event_type", "event"),
                               "event_date": ev.strftime("%Y-%m-%d"), "source": item["source"]})

    by_ticker, evidence = {}, []
    for t in watchlist:
        items = [i for i in fresh if i["ticker"] == t]
        score = sum({"positive": 1, "negative": -1}.get(by_id[i["id"]]["sentiment"], 0) for i in items)
        by_ticker[t] = {
            "sentiment": "positive" if score > 0 else "negative" if score < 0 else "neutral",
            "score": score,
            "items": [{"id": i["id"], "headline": i["headline"], "published_at": i["published_at"],
                       "sentiment": by_id[i["id"]]["sentiment"], "kind": by_id[i["id"]]["kind"]}
                      for i in items],
            "upcoming_events": [e for e in events if e["ticker"] == t],
        }
        for i in items:
            c = by_id[i["id"]]
            evidence.append({"claim": f"{t} [{c['kind']}, {c['sentiment']}]: {i['headline']}",
                             "source": f"{i['source']} {i['url']} ({i['published_at']})"})

    risks = []
    if stale:
        risks.append(f"Ignored {len(stale)} stale item(s) older than {cfg['lookback_days']} days.")
    if duplicates:
        risks.append(f"Ignored {len(duplicates)} duplicate headline(s).")
    for e in events:
        risks.append(f"Scheduled {e['event_type']} for {e['ticker']} on {e['event_date']}.")
    opinions = sum(1 for i in fresh if by_id[i["id"]]["kind"] != "fact")
    if opinions:
        risks.append(f"{opinions} item(s) not classified as confirmed fact.")

    summary = "; ".join(f"{t}: {v['sentiment']} ({len(v['items'])} item(s))" for t, v in by_ticker.items())
    return make_report(
        "news", ctx.as_of_str, "complete", f"News reviewed via {method}. {summary}",
        evidence=evidence, risks=risks, next_step="Charts reviews the same watchlist.",
        data={"by_ticker": by_ticker, "method": method, "stale_ids": stale, "duplicate_ids": duplicates},
    )
