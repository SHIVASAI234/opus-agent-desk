"""Read-only data adapters for market prices, news and earnings dates.

Every adapter returns data in one standard shape, so the agents never know
or care which provider it came from. API keys are read from environment
variables and are never written to logs, reports or the manifest.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import requests

# Environment variable that holds each provider's key.
KEY_ENV = {
    "polygon": "POLYGON_API_KEY",
    "twelvedata": "TWELVEDATA_API_KEY",
    "finnhub": "FINNHUB_API_KEY",
    "alphavantage": "ALPHAVANTAGE_API_KEY",
    "newsapi": "NEWSAPI_API_KEY",
    "marketaux": "MARKETAUX_API_KEY",
}


class ProviderError(RuntimeError):
    """A provider call failed. Messages never include the API key."""


class Throttle:
    """Keeps calls to each provider under its rate limit and daily budget."""

    def __init__(self, min_interval: dict[str, float], daily_budget: dict[str, int],
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.min_interval = min_interval
        self.daily_budget = daily_budget
        self.sleep = sleep
        self.clock = clock
        self.last: dict[str, float] = {}
        self.calls: dict[str, int] = {}

    def wait(self, provider: str) -> None:
        budget = self.daily_budget.get(provider)
        if budget is not None and self.calls.get(provider, 0) >= budget:
            raise ProviderError(f"{provider}: daily call budget ({budget}) used up; skipped")
        gap = self.min_interval.get(provider, 0.0)
        if provider in self.last:
            remaining = gap - (self.clock() - self.last[provider])
            if remaining > 0:
                self.sleep(remaining)
        self.last[provider] = self.clock()
        self.calls[provider] = self.calls.get(provider, 0) + 1


def get_json(session: Any, provider: str, url: str, params: dict, throttle: Throttle | None = None,
             timeout: int = 30) -> Any:
    if throttle is not None:
        throttle.wait(provider)
    try:
        resp = session.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise ProviderError(f"{provider}: network error ({type(exc).__name__})") from None
    code = resp.status_code
    if code == 429:
        raise ProviderError(f"{provider}: rate limited (HTTP 429)")
    if code in (401, 403):
        raise ProviderError(f"{provider}: access denied (HTTP {code}) - check the key or whether your plan covers this endpoint")
    if code >= 400:
        raise ProviderError(f"{provider}: HTTP {code}")
    try:
        return resp.json()
    except ValueError:
        raise ProviderError(f"{provider}: response was not JSON") from None


def short(msg: Any, n: int = 160) -> str:
    text = str(msg)
    return text if len(text) <= n else text[: n - 3] + "..."
