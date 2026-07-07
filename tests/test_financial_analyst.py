"""
10-K Filing Analyst agent — unit tests (offline, no network/LLM).

Covers the deterministic logic (safe_eval, lenient JSON parsing) and the agent
task end-to-end with traced_generation / span / tool monkeypatched, so the
trajectory + tool-use behavior is verified without API calls.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import agents.financial_analyst as fa
from agents.financial_analyst import safe_eval, _parse_json, run_10k_analyst


# --------------- safe_eval ---------------

class TestSafeEval:
    def test_basic_arithmetic(self):
        assert safe_eval("6489 / ((253 + 282) / 2)") == pytest.approx(24.258, abs=0.01)

    def test_operators(self):
        assert safe_eval("(1493602 - 903095) / 903095 * 100") == pytest.approx(65.4, abs=0.1)

    def test_unary_minus(self):
        assert safe_eval("-5 + 3") == -2

    @pytest.mark.parametrize("expr", [
        "__import__('os').system('ls')",
        "revenue / ppe",          # names not allowed
        "open('x')",
        "1 if True else 2",
        "2 ** 3",                 # exponentiation intentionally unsupported (DoS guard)
        "9 ** 9 ** 9",            # would hang the worker if Pow were allowed
    ])
    def test_rejects_non_arithmetic(self, expr):
        with pytest.raises(Exception):
            safe_eval(expr)


# --------------- _parse_json ---------------

class TestParseJson:
    def test_plain(self):
        assert _parse_json('{"a": 1}', {}) == {"a": 1}

    def test_markdown_fenced(self):
        assert _parse_json('```json\n{"a": 1}\n```', {}) == {"a": 1}

    def test_prose_around(self):
        assert _parse_json('Sure! {"a": 1} hope that helps', {}) == {"a": 1}

    def test_default_on_garbage(self):
        assert _parse_json("not json at all", {"fallback": True}) == {"fallback": True}

    def test_default_on_non_object_json(self):
        # Valid JSON but not an object -> treated as a parse failure so callers can
        # safely .get() the result (no AttributeError on a list/scalar).
        assert _parse_json("[1, 2, 3]", {"fallback": True}) == {"fallback": True}
        assert _parse_json("42", {"fallback": True}) == {"fallback": True}


# --------------- Agent task (LLM monkeypatched) ---------------

def _patch_llm(monkeypatch, responses):
    """responses: dict span-name -> returned text. Also stubs span/tool ctx mgrs."""
    def fake_generation(*, name, system, user, model, max_tokens=1024):
        return responses[name]

    class _Ctx:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def update(self, **kw): pass

    monkeypatch.setattr(fa, "traced_generation", fake_generation)
    monkeypatch.setattr(fa, "traced_span", lambda name, **k: _Ctx())
    monkeypatch.setattr(fa, "traced_tool", lambda name, **k: _Ctx())
    # compose uses get_managed_prompt -> force the hardcoded fallback path
    monkeypatch.setattr(fa, "get_managed_prompt", lambda name, fallback: None)


def test_numerical_question_invokes_calculator(monkeypatch):
    _patch_llm(monkeypatch, {
        "plan": '{"needs_calc": true, "line_items": ["Total net revenues", "PP&E net"], "approach": "revenue / avg PP&E"}',
        "extract-operands": '{"expression": "6489 / ((253 + 282) / 2)", "operands": {"revenue": 6489, "ppe_2019": 253, "ppe_2018": 282}, "extracted_value": "", "citations": ["Total net revenues", "Property and equipment, net"]}',
        "compose-answer": "The FY2019 fixed asset turnover ratio is 24.26.",
    })
    task = run_10k_analyst(model="claude-sonnet-4-6")
    out = task(item={"input": {"question": "FY2019 fixed asset turnover?",
                               "evidence": ["Total net revenues 6,489 ... PP&E net 253 / 282"]},
                     "metadata": {"question_reasoning": "Numerical reasoning"}})

    assert out["answer"] == "The FY2019 fixed asset turnover ratio is 24.26."
    traj = out["trajectory"]
    assert "calculate" in traj["tools_used"]
    assert traj["question_type"] == "Numerical reasoning"
    assert traj["steps"] == ["plan", "retrieve-evidence", "compose-answer"]
    assert traj["operands"]["revenue"] == 6489
    assert traj["citations"]


def test_extraction_question_skips_calculator(monkeypatch):
    _patch_llm(monkeypatch, {
        "plan": '{"needs_calc": false, "line_items": ["Net income"], "approach": "extract net income"}',
        "extract-operands": '{"expression": "", "operands": {"net_income": 11588}, "extracted_value": "$11,588 million", "citations": ["Net income"]}',
        "compose-answer": "Amazon's FY2019 net income was $11,588 million.",
    })
    task = run_10k_analyst(model="claude-sonnet-4-6")
    out = task(item={"input": {"question": "FY2019 net income?",
                               "evidence": ["Net income $ 11,588"]},
                     "metadata": {"question_reasoning": "Information extraction"}})

    assert "11,588" in out["answer"]
    assert out["trajectory"]["tools_used"] == []  # no calculator for pure extraction


def test_numerical_with_no_expression_does_not_mark_calculate(monkeypatch):
    # If extraction fails to produce an expression on a numerical question, the
    # calculator is not invoked -> tool_use_correctness will (correctly) flag it.
    _patch_llm(monkeypatch, {
        "plan": '{"needs_calc": true, "line_items": [], "approach": "ratio"}',
        "extract-operands": '{"expression": "", "operands": {}, "extracted_value": "", "citations": []}',
        "compose-answer": "Unable to compute.",
    })
    task = run_10k_analyst(model="claude-sonnet-4-6")
    out = task(item={"input": {"question": "some ratio?", "evidence": ["..."]},
                     "metadata": {"question_reasoning": "Numerical reasoning"}})
    assert "calculate" not in out["trajectory"]["tools_used"]
