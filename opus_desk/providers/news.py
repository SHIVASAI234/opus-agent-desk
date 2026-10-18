"""News and earnings-date adapters. Each returns a list of standard news items:

    {id, ticker, headline, source, url, published_at, provider, provider_sentiment?}

Earnings dates become items with event_type="earnings" and event_date, which the
Risk agent uses for its event blackout.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from . import ProviderError, Throttle, get_json, short


def _id(provider: str, url: str, ticker: str) -> str:
    return f"{provider}-{ticker}-{hashlib.sha1(url.encode()).hexdigest()[:10]}"


def _item(provider: str, ticker: str, headline: str, source: str, url: str, published: datetime,
          sentiment: str | None = None) -> dict:
    item = {
        "id": _id(provider, url, ticker), "ticker": ticker, "headline": (headline or "").strip(),
        "source": (source or "").strip(), "url": url or "",
        "published_at": published.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "provider": provider,
    }
    if sentiment in ("positive", "negative", "neutral"):
        item["provider_sentiment"] = sentiment
    return item


def _score_to_label(score: float | None, threshold: float = 0.15) -> str | None:
    if score is None:
        return None
    return "positive" if score > threshold else "negative" if score < -threshold else "neutral"


def fetch_newsapi(session, ticker: str, query: str, start: date, end: date, key: str,
                  throttle: Throttle | None = None) -> list[dict]:
    # The free developer plan only searches roughly the last month.
    start = max(start, end - timedelta(days=29))
    data = get_json(session, "newsapi", "https://newsapi.org/v2/everything", {
        "q": f'"{query}"', "from": start.isoformat(), "to": end.isoformat(), "language": "en",
        "sortBy": "publishedAt", "pageSize": 100, "apiKey": key}, throttle)
    if data.get("status") != "ok":
        raise ProviderError(f"newsapi: {short(data.get('message') or data.get('code'))}")
    return [_item("newsapi", ticker, a.get("title"), (a.get("source") or {}).get("name"), a.get("url"),
                  datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00")))
            for a in data.get("articles") or [] if a.get("publishedAt")]


def fetch_finnhub_news(session, ticker: str, query: str, start: date, end: date, key: str,
                       throttle: Throttle | None = None, chunk_days: int = 30) -> list[dict]:
    out, cur = [], start
    while cur <= end:
        stop = min(end, cur + timedelta(days=chunk_days - 1))
        data = get_json(session, "finnhub", "https://finnhub.io/api/v1/company-news", {
            "symbol": ticker, "from": cur.isoformat(), "to": stop.isoformat(), "token": key}, throttle)
        if isinstance(data, dict):
            raise ProviderError(f"finnhub: {short(data.get('error') or data)}")
        out += [_item("finnhub", ticker, a.get("headline"), a.get("source"), a.get("url"),
                      datetime.fromtimestamp(a["datetime"], tz=timezone.utc))
                for a in data if a.get("datetime")]
        cur = stop + timedelta(days=1)
    return out


def fetch_marketaux(session, ticker: str, query: str, start: date, end: date, key: str,
                    throttle: Throttle | None = None) -> list[dict]:
    data = get_json(session, "marketaux", "https://api.marketaux.com/v1/news/all", {
        "symbols": ticker, "filter_entities": "true", "language": "en",
        "published_after": f"{start.isoformat()}T00:00", "published_before": f"{end.isoformat()}T23:59",
        "api_token": key}, throttle)
    if "error" in data:
        raise ProviderError(f"marketaux: {short((data['error'] or {}).get('message', data['error']))}")
    out = []
    for a in data.get("data") or []:
        score = next((e.get("sentiment_score") for e in a.get("entities") or [] if e.get("symbol") == ticker), None)
        out.append(_item("marketaux", ticker, a.get("title"), a.get("source"), a.get("url"),
                         datetime.fromisoformat(a["published_at"].replace("Z", "+00:00")),
                         _score_to_label(score)))
    return out


AV_LABELS = {"Bullish": "positive", "Somewhat-Bullish": "positive", "Neutral": "neutral",
             "Somewhat-Bearish": "negative", "Bearish": "negative"}


def fetch_alphavantage_news(session, ticker: str, query: str, start: date, end: date, key: str,
                            throttle: Throttle | None = None) -> list[dict]:
    data = get_json(session, "alphavantage", "https://www.alphavantage.co/query", {
        "function": "NEWS_SENTIMENT", "tickers": ticker, "time_from": start.strftime("%Y%m%dT0000"),
        "time_to": end.strftime("%Y%m%dT2359"), "sort": "LATEST", "limit": 1000, "apikey": key}, throttle)
    for k in ("Error Message", "Note", "Information"):
        if k in data:
            raise ProviderError(f"alphavantage: {short(data[k])}")
    out = []
    for a in data.get("feed") or []:
        label = next((AV_LABELS.get(t.get("ticker_sentiment_label")) for t in a.get("ticker_sentiment") or []
                      if t.get("ticker") == ticker), None)
        published = datetime.strptime(a["time_published"][:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        out.append(_item("alphavantage", ticker, a.get("title"), a.get("source"), a.get("url"), published, label))
    return out


NEWS_FETCHERS = {
    "finnhub": fetch_finnhub_news,
    "alphavantage": fetch_alphavantage_news,
    "marketaux": fetch_marketaux,
    "newsapi": fetch_newsapi,
}


def _event(provider: str, ticker: str, event_date: str, known_days_before: int, doc_url: str) -> dict:
    ev = datetime.strptime(event_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    published = ev - timedelta(days=known_days_before)
    return {
        "id": f"evt-{provider}-{ticker}-{event_date}", "ticker": ticker,
        "headline": f"{ticker} earnings scheduled for {event_date}",
        "source": f"{provider} earnings calendar", "url": doc_url,
        "published_at": published.strftime("%Y-%m-%dT%H:%M:%SZ"), "provider": provider, "kind": "fact",
        "event_type": "earnings", "event_date": event_date,
        "assumption": f"date treated as publicly known {known_days_before} days in advance",
    }


def fetch_earnings_finnhub(session, ticker: str, start: date, end: date, key: str, known_days_before: int,
                           throttle: Throttle | None = None) -> list[dict]:
    data = get_json(session, "finnhub", "https://finnhub.io/api/v1/calendar/earnings", {
        "symbol": ticker, "from": start.isoformat(), "to": end.isoformat(), "token": key}, throttle)
    if data.get("error"):
        raise ProviderError(f"finnhub: {short(data['error'])}")
    return [_event("finnhub", ticker, e["date"], known_days_before, "https://finnhub.io/docs/api/earnings-calendar")
            for e in data.get("earningsCalendar") or [] if e.get("date")]


def fetch_earnings_alphavantage(session, ticker: str, start: date, end: date, key: str, known_days_before: int,
                                throttle: Throttle | None = None) -> list[dict]:
    data = get_json(session, "alphavantage", "https://www.alphavantage.co/query",
                    {"function": "EARNINGS", "symbol": ticker, "apikey": key}, throttle)
    for k in ("Error Message", "Note", "Information"):
        if k in data:
            raise ProviderError(f"alphavantage: {short(data[k])}")
    return [_event("alphavantage", ticker, q["reportedDate"], known_days_before,
                   "https://www.alphavantage.co/documentation/#earnings")
            for q in data.get("quarterlyEarnings") or []
            if q.get("reportedDate") and start.isoformat() <= q["reportedDate"] <= end.isoformat()]


EARNINGS_FETCHERS = {"finnhub": fetch_earnings_finnhub, "alphavantage": fetch_earnings_alphavantage}


def dedupe(items: list[dict]) -> tuple[list[dict], int]:
    """Drop repeats of the same URL or the same headline for the same ticker."""
    seen_url, seen_head, out = set(), set(), []
    for it in sorted(items, key=lambda x: x["published_at"]):
        url_key = (it["ticker"], it["url"])
        head_key = (it["ticker"], it["headline"].lower())
        if url_key in seen_url or head_key in seen_head:
            continue
        seen_url.add(url_key)
        seen_head.add(head_key)
        out.append(it)
    return out, len(items) - len(out)
