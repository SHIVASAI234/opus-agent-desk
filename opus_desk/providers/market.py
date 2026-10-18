"""Daily OHLCV adapters. Each returns a DataFrame: date, open, high, low, close, volume.

Adjustment matters: unadjusted prices show stock splits as fake crashes.
  polygon     -> requested split-adjusted (adjusted=true)
  twelvedata  -> provider default
  finnhub     -> provider default (candles may need a paid plan)
  alphavantage-> TIME_SERIES_DAILY is unadjusted on the free tier
`quality_checks` flags suspicious jumps so you can see if this bit you.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import pandas as pd

from . import ProviderError, Throttle, get_json, short

COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def _frame(rows: list[dict], provider: str, ticker: str) -> pd.DataFrame:
    if not rows:
        raise ProviderError(f"{provider}: no price data returned for {ticker}")
    df = pd.DataFrame(rows, columns=COLUMNS)
    for c in COLUMNS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    if df.empty:
        raise ProviderError(f"{provider}: price data for {ticker} had no usable rows")
    return df


def fetch_polygon(session, ticker: str, start: date, end: date, key: str,
                  throttle: Throttle | None = None, base_url: str = "https://api.polygon.io") -> pd.DataFrame:
    url = f"{base_url}/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}"
    data = get_json(session, "polygon", url,
                    {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key}, throttle)
    if data.get("status") not in ("OK", "DELAYED"):
        raise ProviderError(f"polygon: {short(data.get('error') or data.get('message') or data.get('status'))}")
    rows = [{"date": datetime.fromtimestamp(r["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
             "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"]}
            for r in data.get("results") or []]
    return _frame(rows, "polygon", ticker)


def fetch_twelvedata(session, ticker: str, start: date, end: date, key: str,
                     throttle: Throttle | None = None) -> pd.DataFrame:
    data = get_json(session, "twelvedata", "https://api.twelvedata.com/time_series", {
        "symbol": ticker, "interval": "1day", "start_date": start.isoformat(), "end_date": end.isoformat(),
        "outputsize": 5000, "order": "ASC", "apikey": key}, throttle)
    if data.get("status") == "error":
        raise ProviderError(f"twelvedata: {short(data.get('message'))}")
    rows = [{"date": v["datetime"][:10], "open": v["open"], "high": v["high"], "low": v["low"],
             "close": v["close"], "volume": v.get("volume")} for v in data.get("values") or []]
    return _frame(rows, "twelvedata", ticker)


def fetch_finnhub(session, ticker: str, start: date, end: date, key: str,
                  throttle: Throttle | None = None) -> pd.DataFrame:
    to_unix = lambda d: int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp())  # noqa: E731
    data = get_json(session, "finnhub", "https://finnhub.io/api/v1/stock/candle", {
        "symbol": ticker, "resolution": "D", "from": to_unix(start), "to": to_unix(end) + 86399, "token": key},
        throttle)
    if isinstance(data, dict) and data.get("error"):
        raise ProviderError(f"finnhub: {short(data['error'])}")
    if data.get("s") != "ok":
        raise ProviderError(f"finnhub: status {data.get('s')!r} for {ticker}")
    rows = [{"date": datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d"),
             "open": o, "high": h, "low": lo, "close": c, "volume": v}
            for t, o, h, lo, c, v in zip(data["t"], data["o"], data["h"], data["l"], data["c"], data["v"])]
    return _frame(rows, "finnhub", ticker)


def fetch_alphavantage(session, ticker: str, start: date, end: date, key: str,
                       throttle: Throttle | None = None, outputsize: str = "full") -> pd.DataFrame:
    data = get_json(session, "alphavantage", "https://www.alphavantage.co/query", {
        "function": "TIME_SERIES_DAILY", "symbol": ticker, "outputsize": outputsize, "apikey": key}, throttle)
    for k in ("Error Message", "Note", "Information"):
        if k in data:
            raise ProviderError(f"alphavantage: {short(data[k])}")
    series = data.get("Time Series (Daily)") or {}
    rows = [{"date": d, "open": v["1. open"], "high": v["2. high"], "low": v["3. low"],
             "close": v["4. close"], "volume": v["5. volume"]}
            for d, v in series.items() if start.isoformat() <= d <= end.isoformat()]
    return _frame(rows, "alphavantage", ticker)


FETCHERS = {
    "polygon": fetch_polygon,
    "twelvedata": fetch_twelvedata,
    "finnhub": fetch_finnhub,
    "alphavantage": fetch_alphavantage,
}


def quality_checks(df: pd.DataFrame, max_daily_move: float = 0.4) -> list[str]:
    """Flag rows a reconciliation analyst would question before trusting the data."""
    issues = []
    bad_hl = df[(df["high"] < df["low"]) | (df["close"] > df["high"] * 1.0001) | (df["close"] < df["low"] * 0.9999)]
    if len(bad_hl):
        issues.append(f"{len(bad_hl)} bar(s) where close is outside high-low range")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        issues.append("non-positive prices present")
    rets = df["close"].pct_change().abs()
    jumps = df.loc[rets > max_daily_move, "date"].tolist()
    if jumps:
        issues.append(f"daily move >{max_daily_move:.0%} on {', '.join(map(str, jumps[:5]))} "
                      "(possible unadjusted split - check before trusting a backtest)")
    gaps = pd.to_datetime(df["date"]).diff().dt.days
    if (gaps > 7).any():
        issues.append(f"{int((gaps > 7).sum())} gap(s) longer than a week")
    return issues


def reconcile(frames: dict[str, pd.DataFrame], tolerance_pct: float) -> tuple[pd.DataFrame, list[dict]]:
    """Compare closes across providers on shared dates. Returns (breaks, summary per pair)."""
    names = list(frames)
    breaks, summary = [], []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            m = frames[a][["date", "close"]].merge(frames[b][["date", "close"]], on="date", suffixes=(f"_{a}", f"_{b}"))
            if m.empty:
                summary.append({"pair": f"{a} vs {b}", "shared_days": 0, "breaks": 0, "max_diff_pct": None})
                continue
            diff = (m[f"close_{a}"] - m[f"close_{b}"]).abs() / m[[f"close_{a}", f"close_{b}"]].mean(axis=1) * 100
            m["diff_pct"] = diff.round(4)
            out = m[m["diff_pct"] > tolerance_pct]
            for _, r in out.iterrows():
                breaks.append({"date": r["date"], "provider_a": a, "close_a": r[f"close_{a}"],
                               "provider_b": b, "close_b": r[f"close_{b}"], "diff_pct": r["diff_pct"]})
            summary.append({"pair": f"{a} vs {b}", "shared_days": len(m), "breaks": len(out),
                            "max_diff_pct": round(float(diff.max()), 4)})
    return pd.DataFrame(breaks), summary


def fetch_with_fallback(session, ticker: str, start: date, end: date, providers: list[str],
                        keys: dict[str, str], throttle: Throttle | None = None) -> tuple[pd.DataFrame | None, str | None, list[str]]:
    """Try each provider in order. Returns (frame, provider_used, errors)."""
    errors: list[str] = []
    for p in providers:
        if not keys.get(p):
            continue
        try:
            return FETCHERS[p](session, ticker, start, end, keys[p], throttle), p, errors
        except ProviderError as exc:
            errors.append(str(exc))
    return None, None, errors

