"""The six agents. Each takes a RunContext plus earlier reports and returns one envelope.

Which agents use AI?
  scout, charts, strategy, risk, boss -> deterministic Python (maths and rules)
  news                                 -> optional LLM for reading unstructured text
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class Position:
    ticker: str
    qty: int
    entry_price: float


@dataclass
class RunContext:
    as_of: pd.Timestamp
    prices: dict[str, pd.DataFrame]           # already sliced to <= as_of
    news: list[dict[str, Any]]                # already filtered to <= as_of
    cfg: dict[str, Any]
    equity: float
    open_positions: list[Position] = field(default_factory=list)
    llm_client: Any = None                    # only used by News when use_llm is true

    @property
    def as_of_str(self) -> str:
        return self.as_of.strftime("%Y-%m-%d")

    @property
    def data_label(self) -> str:
        return self.cfg["data"]["label"]
