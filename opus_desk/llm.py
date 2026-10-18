"""Optional Claude integration, used only by the News agent.

Off by default (config: news.use_llm: false). When on, the model classifies
headlines it is given; it never sees prices, never sizes positions, and never
decides anything. Its output is schema-checked and retried once on failure.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator

from .config import ROOT

NEWS_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "sentiment", "kind"],
                "properties": {
                    "id": {"type": "string"},
                    "sentiment": {"enum": ["positive", "negative", "neutral"]},
                    "kind": {"enum": ["fact", "opinion", "unverified"]},
                    "note": {"type": "string"},
                },
            },
        }
    },
}
_validator = Draft202012Validator(NEWS_CLASSIFICATION_SCHEMA)


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class ClaudeClient:
    """Thin wrapper around the Anthropic SDK. Requires ANTHROPIC_API_KEY."""

    def __init__(self, model: str, max_tokens: int):
        try:
            import anthropic  # imported lazily so the core app has no AI dependency
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pip install -r requirements-llm.txt to use the LLM option") from exc
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set (put it in .env)")
        self._client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, system: str, user: str) -> str:  # pragma: no cover - network
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(block.text for block in msg.content if block.type == "text")


def news_prompt() -> str:
    return (ROOT / "prompts" / "news.md").read_text()


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


def classify_news(client: LLMClient, items: list[dict[str, Any]]) -> tuple[list[dict] | None, str]:
    """Return (classifications, error). Retries once with the validation error."""
    payload = [
        {"id": i["id"], "ticker": i["ticker"], "headline": i["headline"], "source": i["source"]}
        for i in items
    ]
    user = "Classify these items. Return JSON only.\n" + json.dumps(payload, indent=2)
    system = news_prompt()
    error = ""
    for attempt in range(2):
        prompt = user if attempt == 0 else f"{user}\n\nYour previous reply was invalid: {error}\nReturn valid JSON only."
        raw = client.complete(system, prompt)
        try:
            obj = json.loads(_strip_fences(raw))
            errs = list(_validator.iter_errors(obj))
            if errs:
                raise ValueError("; ".join(e.message for e in errs))
            ids = {p["id"] for p in payload}
            returned = {c["id"] for c in obj["items"]}
            if returned != ids:
                raise ValueError(f"expected ids {sorted(ids)}, got {sorted(returned)}")
            return obj["items"], ""
        except (json.JSONDecodeError, ValueError) as exc:
            error = str(exc)
    return None, error
