"""Plain technical indicators. Deterministic maths, no AI needed."""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI, 0-100."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss
    out = 100 - 100 / (1 + rs)
    return out.where(avg_loss != 0, 100.0)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range (Wilder smoothing)."""
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def zscore_last(series: pd.Series, lookback: int) -> float | None:
    """How unusual is the latest value vs the previous `lookback` values?"""
    if len(series) < lookback + 1:
        return None
    window = series.iloc[-(lookback + 1):-1]
    std = window.std()
    if std == 0 or pd.isna(std):
        return None
    return float((series.iloc[-1] - window.mean()) / std)
