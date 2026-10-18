"""Malformed JSON, missing timestamps, missing sources, and the LLM retry rule."""
import json

import pytest

from opus_desk.llm import classify_news
from opus_desk.schema import ReportValidationError, make_report, parse_json_report, validate_report


def good_report(**overrides):
    rep = make_report("scout", "2026-09-30", "complete", "ok",
                      evidence=[{"claim": "x", "source": "test"}])
    rep.update(overrides)
    return rep


def test_valid_report_passes():
    validate_report(good_report())


def test_malformed_json_is_rejected():
    with pytest.raises(ReportValidationError, match="malformed JSON"):
        parse_json_report('{"agent": "scout", "status": "complete",')  # truncated


def test_non_object_json_is_rejected():
    with pytest.raises(ReportValidationError):
        parse_json_report("[1, 2, 3]")


def test_missing_timestamp_is_rejected():
    rep = good_report()
    del rep["timestamp_utc"]
    with pytest.raises(ReportValidationError, match="timestamp_utc"):
        validate_report(rep)


def test_bad_timestamp_format_is_rejected():
    with pytest.raises(ReportValidationError):
        validate_report(good_report(timestamp_utc="yesterday afternoon, roughly"))


def test_evidence_without_source_is_rejected():
    with pytest.raises(ReportValidationError, match="source"):
        validate_report(good_report(evidence=[{"claim": "price went up"}]))


def test_unknown_status_is_rejected():
    with pytest.raises(ReportValidationError):
        validate_report(good_report(status="probably_fine"))


def test_boss_decision_is_restricted():
    rep = make_report("boss", "2026-09-30", "complete", "go", data={"decision": "BUY_NOW"})
    with pytest.raises(ReportValidationError, match="decision"):
        validate_report(rep)


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, user):
        self.calls += 1
        return self.replies.pop(0)


ITEMS = [{"id": "N1", "ticker": "TEST_AAA", "headline": "TEST_AAA beats estimates", "source": "Test Wire"}]
GOOD = json.dumps({"items": [{"id": "N1", "sentiment": "positive", "kind": "fact"}]})


def test_llm_retries_once_then_succeeds():
    llm = FakeLLM(["not json at all", GOOD])
    out, err = classify_news(llm, ITEMS)
    assert llm.calls == 2 and out[0]["sentiment"] == "positive" and err == ""


def test_llm_two_failures_returns_none():
    llm = FakeLLM(["{broken", '{"items": [{"id": "N1", "sentiment": "very bullish", "kind": "fact"}]}'])
    out, err = classify_news(llm, ITEMS)
    assert out is None and llm.calls == 2 and err


def test_llm_cannot_invent_or_drop_items():
    llm = FakeLLM([json.dumps({"items": []}), json.dumps({"items": [
        {"id": "N1", "sentiment": "neutral", "kind": "fact"},
        {"id": "N99", "sentiment": "positive", "kind": "fact"}]})])
    out, _ = classify_news(llm, ITEMS)
    assert out is None
