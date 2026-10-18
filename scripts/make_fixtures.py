"""Generate the synthetic TEST DATA used by default.

Everything here is fictional: tickers start with TEST_, news URLs point at
example.com, and the prices are random walks with regime changes.
Re-running with the same seed reproduces the same files.

    python scripts/make_fixtures.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT_PRICES = ROOT / "data" / "fixtures" / "prices"
OUT_NEWS = ROOT / "data" / "fixtures" / "news.json"
SEED = 7

TICKERS = {
    # name: (start price, base daily shares, beta to shared factor)
    "TEST_AAA": (120.0, 2_500_000, 0.9),
    "TEST_BBB": (85.0, 1_800_000, 0.9),   # moves with AAA -> tests the correlation check
    "TEST_CCC": (42.0, 3_000_000, 0.3),
    "TEST_DDD": (210.0, 900_000, 0.5),
    "TEST_EEE": (18.0, 40_000, 0.2),      # thinly traded -> tests the liquidity check
}


def make_prices(rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    dates = pd.bdate_range("2025-03-03", "2026-09-30")
    n = len(dates)
    market = rng.normal(0.0003, 0.009, n)
    out = {}
    for name, (p0, shares, beta) in TICKERS.items():
        # regime drift changes roughly every 60 days
        drift = np.repeat(rng.choice([0.0018, 0.0, -0.0012], size=n // 60 + 1), 60)[:n]
        idio = rng.normal(0, 0.013, n)
        rets = drift + beta * market + idio
        # occasional news-style jumps
        jumps = rng.random(n) < 0.02
        rets[jumps] += rng.normal(0.0, 0.05, jumps.sum())
        close = p0 * np.exp(np.cumsum(rets))
        prev = np.concatenate([[p0], close[:-1]])
        open_ = prev * np.exp(rng.normal(0, 0.004, n))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
        surprise = np.abs(rets) / 0.013
        volume = shares * np.exp(rng.normal(0, 0.25, n)) * (1 + 0.6 * surprise)
        out[name] = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"), "open": open_.round(4), "high": high.round(4),
            "low": low.round(4), "close": close.round(4), "volume": volume.round(0).astype(int),
        })
    return out


def make_news() -> list[dict]:
    def item(i, ticker, headline, ts, kind="fact", source="Test Wire", event_type=None, event_date=None):
        return {"id": f"N{i:03d}", "ticker": ticker, "headline": headline, "source": source,
                "url": f"https://example.com/test-news/{i}", "published_at": ts, "kind": kind,
                "event_type": event_type, "event_date": event_date, "label": "TEST DATA - fictional"}

    return [
        item(1, "TEST_AAA", "TEST_AAA beats quarterly revenue estimates", "2025-11-04T13:00:00Z"),
        item(2, "TEST_BBB", "TEST_BBB announces partnership with fictional retailer", "2026-02-10T14:30:00Z"),
        item(3, "TEST_CCC", "Regulator opens probe into TEST_CCC billing practices", "2026-03-18T09:15:00Z"),
        item(4, "TEST_DDD", "Columnist: TEST_DDD looks expensive here", "2026-04-22T11:00:00Z",
             kind="opinion", source="Test Opinion Desk"),
        item(5, "TEST_AAA", "TEST_AAA raises full-year guidance", "2026-05-06T12:00:00Z"),
        item(6, "TEST_EEE", "TEST_EEE wins small municipal contract", "2026-06-02T15:00:00Z"),
        item(7, "TEST_CCC", "TEST_CCC warns on margins after supplier delay", "2026-07-14T10:00:00Z"),
        item(8, "TEST_BBB", "Unnamed sources say TEST_BBB exploring sale", "2026-08-05T16:45:00Z",
             kind="unverified", source="Test Rumour Mill"),
        item(9, "TEST_BBB", "Unnamed sources say TEST_BBB exploring sale", "2026-08-05T17:10:00Z",
             kind="unverified", source="Test Rumour Mill"),  # duplicate headline
        item(10, "TEST_DDD", "TEST_DDD schedules Q3 earnings call", "2026-09-22T12:00:00Z",
             event_type="earnings", event_date="2026-10-06"),
        item(11, "TEST_AAA", "TEST_AAA schedules Q3 earnings call", "2026-09-24T12:00:00Z",
             event_type="earnings", event_date="2026-10-20"),
        item(12, "TEST_CCC", "TEST_CCC upgraded by fictional broker", "2026-09-28T08:30:00Z",
             kind="opinion", source="Test Broker Notes"),
        item(13, "TEST_EEE", "TEST_EEE completes buyback programme", "2026-09-29T13:00:00Z"),
    ]


def main() -> None:
    rng = np.random.default_rng(SEED)
    OUT_PRICES.mkdir(parents=True, exist_ok=True)
    for name, df in make_prices(rng).items():
        df.to_csv(OUT_PRICES / f"{name}.csv", index=False)
    OUT_NEWS.write_text(json.dumps(make_news(), indent=2) + "\n")
    print(f"Wrote {len(TICKERS)} price files to {OUT_PRICES.relative_to(ROOT)} and news to {OUT_NEWS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
