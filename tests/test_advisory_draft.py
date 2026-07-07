"""
Client Advisory Drafting agent — unit tests (offline, no network/LLM).

Covers the 3-span flow, the deterministic compliance self-check, and — most
importantly — the compliance *gate* FAIL story: a draft containing a prohibited
phrase drops regulatory_compliance to 0.0, which fails the multi-dimensional gate
even when groundedness and completeness are perfect. This is the deterministic
proof of the use case (an aligned model rarely emits prohibited phrases
unprompted, so the live FAIL path is best-effort via tempt_noncompliant).
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agents.advisory_draft as ad
from agents.advisory_draft import run_advisory_draft, GATE_ADVISORY_DRAFT
from evaluators import (
    Evaluation,
    regulatory_compliance_evaluator,
    tool_use_correctness_evaluator,
    usecase_certification_gate,
)


def _patch_llm(monkeypatch, responses, capture=None):
    """responses: dict span-name -> returned text. capture: optional dict to record
    the `user` prompt each generation received (for asserting prompt selection)."""
    def fake_generation(*, name, system, user, model, max_tokens=1024):
        if capture is not None:
            capture[name] = user
        return responses[name]

    class _Ctx:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def update(self, **kw): pass

    monkeypatch.setattr(ad, "traced_generation", fake_generation)
    monkeypatch.setattr(ad, "traced_tool", lambda name, **k: _Ctx())
    monkeypatch.setattr(ad, "get_managed_prompt", lambda name, fallback: None)


_ITEM = {"input": {"question": "Summarize FY2019 revenue for a client.",
                   "evidence": ["Total net revenues for FY2019 were $6,489 million."]}}


# --------------- Clean draft (PASS path) ---------------

def test_clean_draft_passes_compliance(monkeypatch):
    _patch_llm(monkeypatch, {
        "analyze": "FY2019 revenue: $6,489M.",
        "draft": "In FY2019 the company reported revenue of $6,489 million.",
    })
    task = run_advisory_draft(model="claude-sonnet-4-6")
    out = task(item=_ITEM)

    traj = out["trajectory"]
    assert traj["question_type"] == "advisory"
    assert traj["steps"] == ["analyze", "draft"]
    assert traj["tools_used"] == ["compliance-self-check"]
    assert traj["compliance_checked"] is True
    assert traj["violations"] == []
    # The self-check agrees with the standalone compliance evaluator.
    assert regulatory_compliance_evaluator(output=out).value == 1.0
    # The shared trajectory rule: advisory requires compliance-self-check.
    assert tool_use_correctness_evaluator(output=out, metadata={}).value == 1.0


# --------------- Non-compliant draft (FAIL path) ---------------

def test_prohibited_phrase_flagged_by_self_check(monkeypatch):
    _patch_llm(monkeypatch, {
        "analyze": "FY2019 revenue grew strongly.",
        "draft": "This is a risk-free investment with guaranteed returns — you can't lose!",
    })
    task = run_advisory_draft(model="claude-sonnet-4-6")
    out = task(item=_ITEM)

    # The in-trace self-check catches its own violations...
    assert "guaranteed returns" in out["trajectory"]["violations"]
    assert out["trajectory"]["compliance_checked"] is True
    # ...and the standalone evaluator scores it 0.0.
    assert regulatory_compliance_evaluator(output=out).value == 0.0


def test_compliance_violation_fails_gate_despite_perfect_other_dims():
    """A single prohibited phrase fails the hard compliance gate even when
    groundedness and completeness are perfect — the core use-case story."""
    item = SimpleNamespace(evaluations=[
        Evaluation(name="groundedness", value=1.0),
        Evaluation(name="completeness", value=1.0),
        Evaluation(name="tool_use_correctness", value=1.0),
        Evaluation(name="regulatory_compliance", value=0.0),  # one violation
    ])
    gate = usecase_certification_gate(GATE_ADVISORY_DRAFT)
    result = gate(item_results=[item])
    assert result.value == 0.0
    assert "FAILED" in result.comment
    assert "regulatory_compliance" in result.comment


# --------------- tempt_noncompliant path selection ---------------

def test_tempt_flag_swaps_in_leading_draft_prompt(monkeypatch):
    capture = {}
    _patch_llm(monkeypatch, {"analyze": "facts", "draft": "some draft"}, capture=capture)
    task = run_advisory_draft(model="claude-sonnet-4-6", tempt_noncompliant=True)
    task(item=_ITEM)
    # The draft step received the leading prompt that tempts prohibited language.
    assert "guaranteed returns" in capture["draft"].lower()


def test_default_uses_compliant_draft_prompt(monkeypatch):
    capture = {}
    _patch_llm(monkeypatch, {"analyze": "facts", "draft": "some draft"}, capture=capture)
    task = run_advisory_draft(model="claude-sonnet-4-6")
    task(item=_ITEM)
    # Default prompt instructs the model to AVOID the prohibited phrases.
    assert "do not" in capture["draft"].lower() or "compliant" in capture["draft"].lower()
