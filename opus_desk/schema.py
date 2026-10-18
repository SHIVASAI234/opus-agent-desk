"""The shared report envelope every agent returns, and its validator.

Every agent output must pass this schema before the next agent sees it.
Evidence items must carry a source, so nothing enters the pipeline unattributed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from jsonschema import Draft202012Validator

AGENT_ORDER = ["scout", "news", "charts", "strategy", "risk", "boss"]
STATUSES = ["complete", "needs_data", "rejected"]
FINAL_DECISIONS = ["APPROVE_FOR_PAPER_TEST", "WAIT", "REJECT"]

ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "agent", "timestamp_utc", "data_as_of", "status",
        "summary", "evidence", "risks", "next_step", "data",
    ],
    "additionalProperties": False,
    "properties": {
        "agent": {"enum": AGENT_ORDER},
        "timestamp_utc": {"type": "string", "format": "date-time", "minLength": 20},
        "data_as_of": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
        "status": {"enum": STATUSES},
        "summary": {"type": "string", "minLength": 1},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim", "source"],
                "properties": {
                    "claim": {"type": "string", "minLength": 1},
                    "source": {"type": "string", "minLength": 1},
                    "value": {},
                },
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "next_step": {"type": "string"},
        "data": {"type": "object"},
    },
}

_validator = Draft202012Validator(ENVELOPE_SCHEMA)


class ReportValidationError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_report(
    agent: str,
    data_as_of: str,
    status: str,
    summary: str,
    evidence: list[dict] | None = None,
    risks: list[str] | None = None,
    next_step: str = "",
    data: dict | None = None,
) -> dict[str, Any]:
    return {
        "agent": agent,
        "timestamp_utc": utc_now(),
        "data_as_of": data_as_of,
        "status": status,
        "summary": summary,
        "evidence": evidence or [],
        "risks": risks or [],
        "next_step": next_step,
        "data": data or {},
    }


def validate_report(report: Any) -> None:
    """Raise ReportValidationError listing every problem found."""
    if not isinstance(report, dict):
        raise ReportValidationError(f"report must be a JSON object, got {type(report).__name__}")
    errors = sorted(_validator.iter_errors(report), key=lambda e: list(e.path))
    if errors:
        msgs = [f"{'/'.join(map(str, e.path)) or '<root>'}: {e.message}" for e in errors]
        raise ReportValidationError("; ".join(msgs))
    # jsonschema doesn't enforce "format" by default, so check the timestamp ourselves.
    try:
        datetime.strptime(report["timestamp_utc"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ReportValidationError(f"timestamp_utc is not ISO-8601 UTC: {exc}") from exc
    if report["agent"] == "boss" and report["data"].get("decision") not in FINAL_DECISIONS:
        raise ReportValidationError(f"boss decision must be one of {FINAL_DECISIONS}")


def parse_json_report(text: str) -> dict[str, Any]:
    """Parse raw model text into a validated report (raises on any problem)."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReportValidationError(f"malformed JSON: {exc}") from exc
    validate_report(obj)
    return obj
