"""Load price and news data, always cut at an as-of date.

`slice_as_of` is the guard against look-ahead bias: agents only ever receive
data dated on or before the decision date.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import ROOT

PRICE_COLUMNS = ["open", "high", "low", "close", "volume"]


def load_prices(prices_dir: str | Path, tickers: list[str]) -> dict[str, pd.DataFrame]:
    prices_dir = ROOT / prices_dir if not Path(prices_dir).is_absolute() else Path(prices_dir)
    out: dict[str, pd.DataFrame] = {}
    for t in tickers:
        path = prices_dir / f"{t}.csv"
        if not path.exists():
            continue  # missing ticker -> agents will report needs_data
        df = pd.read_csv(path, parse_dates=["date"]).set_index("date").sort_index()
        missing = [c for c in PRICE_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"{path.name} missing columns: {missing}")
        out[t] = df[PRICE_COLUMNS].astype(float)
    return out


def load_news(news_file: str | Path) -> list[dict[str, Any]]:
    path = ROOT / news_file if not Path(news_file).is_absolute() else Path(news_file)
    if not path.exists():
        return []
    with open(path) as fh:
        return json.load(fh)


def slice_as_of(prices: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    return {t: df.loc[:as_of] for t, df in prices.items()}


def news_as_of(news: list[dict[str, Any]], as_of: pd.Timestamp) -> list[dict[str, Any]]:
    """Only news published on or before the as-of date (no peeking)."""
    out = []
    for item in news:
        ts = item.get("published_at")
        if ts is None:
            out.append(item)  # keep it so the News agent can flag the missing timestamp
            continue
        if pd.Timestamp(ts).tz_localize(None) <= as_of + pd.Timedelta(hours=23, minutes=59):
            out.append(item)
    return out
