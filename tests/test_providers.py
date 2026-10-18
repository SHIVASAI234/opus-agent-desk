"""Data adapters, tested offline with recorded-style payloads (no network, no real keys)."""
import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest
import requests
import yaml

from opus_desk.providers import ProviderError, Throttle, get_json
from opus_desk.providers import market as mk
from opus_desk.providers import news as nw

SECRET = "sk-test-SECRET-123"


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    """Routes requests by URL fragment to canned payloads and records every call."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for fragment, handler in self.routes.items():
            if fragment in url:
                out = handler(url, params or {}) if callable(handler) else handler
                return out if isinstance(out, Resp) else Resp(out)
        return Resp({"error": "no route"}, 404)


START, END = date(2026, 1, 1), date(2026, 1, 31)


# ---------------- market ----------------

def test_polygon_parses_and_sorts():
    ms = lambda d: int(datetime(2026, 1, d, tzinfo=timezone.utc).timestamp() * 1000)  # noqa: E731
    s = FakeSession({"polygon": {"status": "OK", "results": [
        {"t": ms(6), "o": 11, "h": 12, "l": 10, "c": 11.5, "v": 900},
        {"t": ms(5), "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 1000}]}})
    df = mk.fetch_polygon(s, "AAPL", START, END, SECRET)
    assert list(df["date"]) == ["2026-01-05", "2026-01-06"]
    assert s.calls[0][1]["adjusted"] == "true"


def test_twelvedata_parses_and_reports_errors():
    s = FakeSession({"twelvedata": {"status": "ok", "values": [
        {"datetime": "2026-01-05", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "1000"}]}})
    assert mk.fetch_twelvedata(s, "AAPL", START, END, SECRET)["close"].iloc[0] == 10.5
    bad = FakeSession({"twelvedata": {"status": "error", "message": "symbol not found"}})
    with pytest.raises(ProviderError, match="symbol not found"):
        mk.fetch_twelvedata(bad, "ZZZZ", START, END, SECRET)


def test_finnhub_candles_and_no_data():
    t = int(datetime(2026, 1, 5, tzinfo=timezone.utc).timestamp())
    s = FakeSession({"finnhub": {"s": "ok", "t": [t], "o": [10], "h": [11], "l": [9], "c": [10.5], "v": [1000]}})
    assert len(mk.fetch_finnhub(s, "AAPL", START, END, SECRET)) == 1
    with pytest.raises(ProviderError, match="no_data"):
        mk.fetch_finnhub(FakeSession({"finnhub": {"s": "no_data"}}), "AAPL", START, END, SECRET)


def test_alphavantage_parses_filters_and_flags_limits():
    s = FakeSession({"alphavantage": {"Time Series (Daily)": {
        "2026-01-05": {"1. open": "10", "2. high": "11", "3. low": "9", "4. close": "10.5", "5. volume": "1000"},
        "2025-06-01": {"1. open": "1", "2. high": "1", "3. low": "1", "4. close": "1", "5. volume": "1"}}}})
    df = mk.fetch_alphavantage(s, "AAPL", START, END, SECRET)
    assert list(df["date"]) == ["2026-01-05"]  # out-of-range row dropped
    limited = FakeSession({"alphavantage": {"Information": "Thank you for using Alpha Vantage! premium endpoint"}})
    with pytest.raises(ProviderError, match="premium"):
        mk.fetch_alphavantage(limited, "AAPL", START, END, SECRET)


def test_http_errors_are_clear_and_never_leak_the_key():
    with pytest.raises(ProviderError, match="access denied") as e:
        get_json(FakeSession({"x": Resp({}, 403)}), "polygon", "https://x", {"apiKey": SECRET})
    assert SECRET not in str(e.value)

    class Boom:
        def get(self, url, params=None, timeout=None):
            raise requests.ConnectionError(f"failed {url}?apiKey={params['apiKey']}")

    with pytest.raises(ProviderError) as e:
        get_json(Boom(), "polygon", "https://x", {"apiKey": SECRET})
    assert SECRET not in str(e.value) and SECRET not in repr(e.value.__cause__)


def test_rate_limit_message():
    with pytest.raises(ProviderError, match="429"):
        get_json(FakeSession({"x": Resp({}, 429)}), "finnhub", "https://x", {})


def test_fallback_uses_next_provider():
    s = FakeSession({
        "polygon": Resp({}, 403),
        "twelvedata": {"status": "ok", "values": [
            {"datetime": "2026-01-05", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]},
    })
    df, used, errors = mk.fetch_with_fallback(s, "AAPL", START, END, ["polygon", "twelvedata"],
                                              {"polygon": "k1", "twelvedata": "k2"})
    assert used == "twelvedata" and len(df) == 1 and "access denied" in errors[0]


def test_fallback_skips_providers_without_keys():
    s = FakeSession({})
    df, used, errors = mk.fetch_with_fallback(s, "AAPL", START, END, ["polygon"], {})
    assert df is None and used is None and not s.calls


def test_quality_checks_flag_unadjusted_split():
    df = pd.DataFrame({"date": ["2026-01-05", "2026-01-06"], "open": [100, 10], "high": [101, 11],
                       "low": [99, 9], "close": [100, 10], "volume": [1, 1]})
    assert any("unadjusted split" in i for i in mk.quality_checks(df))


def test_reconcile_finds_breaks():
    a = pd.DataFrame({"date": ["d1", "d2"], "close": [100.0, 100.0]})
    b = pd.DataFrame({"date": ["d1", "d2"], "close": [100.1, 103.0]})
    breaks, summary = mk.reconcile({"polygon": a, "twelvedata": b}, tolerance_pct=0.5)
    assert len(breaks) == 1 and breaks.iloc[0]["date"] == "d2"
    assert summary[0]["shared_days"] == 2 and summary[0]["breaks"] == 1


def test_throttle_enforces_budget_and_spacing():
    slept, now = [], [0.0]
    th = Throttle({"p": 5.0}, {"p": 2}, sleep=lambda s: slept.append(s), clock=lambda: now[0])
    th.wait("p")
    th.wait("p")
    assert slept == [5.0]
    with pytest.raises(ProviderError, match="budget"):
        th.wait("p")


# ---------------- news + earnings ----------------

def test_newsapi_items_and_window_clamp():
    s = FakeSession({"newsapi": {"status": "ok", "articles": [
        {"title": "Apple beats estimates", "url": "https://n/1", "publishedAt": "2026-01-20T10:00:00Z",
         "source": {"name": "Reuters"}}]}})
    items = nw.fetch_newsapi(s, "AAPL", "Apple", date(2025, 1, 1), END, SECRET)
    assert items[0]["source"] == "Reuters" and items[0]["published_at"] == "2026-01-20T10:00:00Z"
    assert s.calls[0][1]["from"] == "2026-01-02"  # clamped to ~30 days for the free plan


def test_finnhub_news_chunks_requests():
    s = FakeSession({"company-news": lambda u, p: [
        {"headline": f"h {p['from']}", "url": f"https://n/{p['from']}", "source": "Yahoo", "datetime": 1767600000}]})
    items = nw.fetch_finnhub_news(s, "AAPL", "Apple", date(2026, 1, 1), date(2026, 3, 1), SECRET, chunk_days=30)
    assert len(s.calls) == 2 and len(items) == 2


def test_marketaux_and_alphavantage_sentiment_mapping():
    mx = FakeSession({"marketaux": {"data": [{"title": "t", "url": "https://n/m", "source": "src",
                                              "published_at": "2026-01-20T10:00:00.000000Z",
                                              "entities": [{"symbol": "AAPL", "sentiment_score": -0.6}]}]}})
    assert nw.fetch_marketaux(mx, "AAPL", "Apple", START, END, SECRET)[0]["provider_sentiment"] == "negative"
    av = FakeSession({"alphavantage": {"feed": [{"title": "t", "url": "https://n/a", "source": "Benzinga",
                                                 "time_published": "20260120T101500",
                                                 "ticker_sentiment": [{"ticker": "AAPL",
                                                                       "ticker_sentiment_label": "Somewhat-Bullish"}]}]}})
    item = nw.fetch_alphavantage_news(av, "AAPL", "Apple", START, END, SECRET)[0]
    assert item["provider_sentiment"] == "positive" and item["published_at"] == "2026-01-20T10:15:00Z"


def test_earnings_become_events_known_in_advance():
    s = FakeSession({"calendar/earnings": {"earningsCalendar": [{"date": "2026-01-29", "symbol": "AAPL"}]}})
    ev = nw.fetch_earnings_finnhub(s, "AAPL", START, END, SECRET, known_days_before=21)[0]
    assert ev["event_type"] == "earnings" and ev["event_date"] == "2026-01-29"
    assert ev["published_at"] == "2026-01-08T00:00:00Z" and ev["url"]


def test_dedupe_by_url_and_headline():
    base = {"ticker": "AAPL", "source": "s", "published_at": "2026-01-01T00:00:00Z"}
    items = [{**base, "headline": "A", "url": "u1"}, {**base, "headline": "A", "url": "u2"},
             {**base, "headline": "B", "url": "u1"}, {**base, "headline": "C", "url": "u3"}]
    out, dropped = nw.dedupe(items)
    assert [i["headline"] for i in out] == ["A", "C"] and dropped == 2


# ---------------- end to end: fetch script -> pipeline -> backtest ----------------

def test_fetch_script_end_to_end(tmp_path, monkeypatch, cfg):
    import scripts.fetch_data as fd
    from opus_desk.backtest import run_backtest
    from opus_desk.pipeline import build_context, run_pipeline

    fixture = pd.read_csv(fd.ROOT / "data/fixtures/prices/TEST_AAA.csv")

    def polygon(url, params):
        ticker = url.split("/ticker/")[1].split("/")[0]
        f = fixture.copy()
        if ticker == "BBB":
            f[["open", "high", "low", "close"]] *= 0.5
        return {"status": "OK", "results": [
            {"t": int(pd.Timestamp(r.date, tz="UTC").timestamp() * 1000), "o": r.open, "h": r.high,
             "l": r.low, "c": r.close, "v": r.volume} for r in f.itertuples()]}

    session = FakeSession({
        "polygon": polygon,
        "company-news": [{"headline": "AAA wins contract", "url": "https://n/1", "source": "Wire",
                          "datetime": 1789000000}],
        "calendar/earnings": {"earningsCalendar": [{"date": "2026-08-20", "symbol": "AAA"}]},
    })
    real_cfg = yaml.safe_load((fd.ROOT / "config_real.yaml").read_text())
    real_cfg.update({"universe": ["AAA", "BBB"], "reports_dir": str(tmp_path / "reports")})
    real_cfg["data"].update({"prices_dir": str(tmp_path / "real/prices"), "news_file": str(tmp_path / "real/news.json")})
    real_cfg["fetch"]["history_years"] = 3
    real_cfg["fetch"]["min_seconds_between_calls"] = {}
    real_cfg["risk"]["min_avg_dollar_volume"] = 1
    cfg_path = tmp_path / "config_real.yaml"
    cfg_path.write_text(yaml.safe_dump(real_cfg))

    monkeypatch.setenv("POLYGON_API_KEY", SECRET)
    monkeypatch.setenv("FINNHUB_API_KEY", SECRET)
    monkeypatch.setattr(fd, "datetime", type("D", (datetime,), {"now": staticmethod(
        lambda tz=None: datetime(2026, 9, 30, 12, tzinfo=timezone.utc))}))

    assert fd.main(["--config", str(cfg_path)], session=session) == 0
    manifest = json.loads((tmp_path / "real/fetch_manifest.json").read_text())
    assert manifest["prices"]["AAA"]["provider"] == "polygon"
    assert SECRET not in json.dumps(manifest)
    news = json.loads((tmp_path / "real/news.json").read_text())
    assert any(i.get("event_type") == "earnings" for i in news)

    loaded = yaml.safe_load(cfg_path.read_text())
    result = run_pipeline(build_context(loaded))
    assert result["decision"] in ("APPROVE_FOR_PAPER_TEST", "WAIT", "REJECT")
    assert list(result["reports"])[0] == "scout"
    summary = run_backtest(loaded, out_dir=tmp_path / "reports")
    assert summary["trading_days"] > 100
    assert any("Survivorship" in w for w in summary["warnings"])
