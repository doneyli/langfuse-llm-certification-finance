"""
Market Sentiment Triage agent — unit tests (offline, no network/LLM).

Covers the deterministic logic (lenient label/confidence parsing, driver-phrase
extraction, confidence-based routing) and the agent task end-to-end with the LLM
call / span / tool monkeypatched. Also pins the FAIL story for this use case
(`sentiment_accuracy` dropping) deterministically, since it cannot be elicited
reliably from a live model.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agents.sentiment_triage as st
from agents.sentiment_triage import (
    _parse_label_conf,
    _extract_driver_phrase,
    run_sentiment_triage,
    ROUTE_THRESHOLD,
)
from evaluators import sentiment_evaluator, tool_use_correctness_evaluator


# --------------- _parse_label_conf ---------------

class TestParseLabelConf:
    def test_pipe_format(self):
        assert _parse_label_conf("negative | 0.82") == ("negative", 0.82)

    def test_bare_label_defaults_confidence(self):
        # No parseable confidence -> low default so the item escalates to a human.
        label, conf = _parse_label_conf("positive")
        assert label == "positive"
        assert conf == 0.5

    def test_percentage_normalized(self):
        assert _parse_label_conf("positive | 82%") == ("positive", 0.82)

    def test_garbage_safe_default(self):
        # Unparseable -> neutral + low confidence (escalate, never silently accept).
        label, conf = _parse_label_conf("???")
        assert label == "neutral"
        assert conf < ROUTE_THRESHOLD

    def test_confidence_clamped(self):
        assert _parse_label_conf("negative | 1.5")[1] == 1.0


# --------------- _extract_driver_phrase ---------------

class TestExtractDriverPhrase:
    def test_prefers_quantitative_sentence(self):
        text = "The outlook is uncertain. Revenue dropped 10 percent year over year."
        assert "10 percent" in _extract_driver_phrase(text, "negative")

    def test_empty(self):
        assert _extract_driver_phrase("", "neutral") == ""


# --------------- Agent task (LLM monkeypatched) ---------------

def _patch_llm(monkeypatch, classify_response):
    def fake_generation(*, name, system, user, model, max_tokens=1024):
        return classify_response

    class _Ctx:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def update(self, **kw): pass

    monkeypatch.setattr(st, "traced_generation", fake_generation)
    monkeypatch.setattr(st, "traced_span", lambda name, **k: _Ctx())
    monkeypatch.setattr(st, "traced_tool", lambda name, **k: _Ctx())
    monkeypatch.setattr(st, "get_managed_prompt", lambda name, fallback: None)


def test_high_confidence_auto_accepts(monkeypatch):
    _patch_llm(monkeypatch, "negative | 0.92")
    task = run_sentiment_triage(model="claude-sonnet-4-6")
    out = task(item={"input": {"text": "Revenue dropped 10 percent from a year earlier."}})

    assert out["answer"] == "negative"
    traj = out["trajectory"]
    assert traj["tools_used"] == ["route"]
    # route is a tool, not a step (mirrors the 10-K agent: calculate is in
    # tools_used, not steps).
    assert traj["steps"] == ["classify", "rationale"]
    assert traj["question_type"] == "sentiment"
    assert traj["operands"]["confidence"] == 0.92
    assert traj["action"] == "auto-accept"
    assert traj["citations"]  # driver phrase recorded


def test_low_confidence_flags_for_analyst(monkeypatch):
    _patch_llm(monkeypatch, "neutral | 0.40")
    task = run_sentiment_triage(model="claude-sonnet-4-6")
    out = task(item={"input": {"text": "The transaction is expected to close next quarter."}})
    assert out["trajectory"]["action"] == "flag-for-analyst"
    assert "route" in out["trajectory"]["tools_used"]  # tool runs on every item


def test_route_tool_present_even_on_parse_failure(monkeypatch):
    _patch_llm(monkeypatch, "I cannot determine the sentiment.")
    task = run_sentiment_triage(model="claude-sonnet-4-6")
    out = task(item={"input": {"text": "Some ambiguous statement."}})
    # Parse failed -> safe default -> still routes (to a human), tool still ran.
    assert out["trajectory"]["tools_used"] == ["route"]
    assert out["trajectory"]["action"] == "flag-for-analyst"


# --------------- FAIL story (deterministic) ---------------

def test_tool_use_correctness_passes_for_sentiment():
    # `route` in tools_used satisfies the sentiment rule in the shared evaluator.
    out = {"answer": "positive", "trajectory": {"question_type": "sentiment",
                                                "tools_used": ["route"]}}
    ev = tool_use_correctness_evaluator(output=out)
    assert ev.value == 1.0


def test_wrong_label_drops_sentiment_accuracy():
    # The only gate dimension that can realistically fail this use case: a weak
    # classifier producing the wrong label tanks sentiment_accuracy below 0.85.
    ev = sentiment_evaluator(output={"answer": "positive", "trajectory": {}},
                             expected_output={"sentiment": "negative"})
    assert ev.value == 0.0
