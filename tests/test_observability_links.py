"""Offline tests for how traces are shaped in Langfuse (no network).

Prompt links (Prompts → Metrics per version), observation types (Agent Graph)
and BOOLEAN pass/fail scores are what make the gate's evidence readable in
Langfuse. These tests pin the wiring without calling Langfuse or an LLM.
"""
from types import SimpleNamespace

import pytest

import agents.base as base


class TestLinkablePrompt:
    def test_managed_prompt_is_linked(self):
        prompt = SimpleNamespace(version=3, is_fallback=False)
        assert base.linkable_prompt(prompt) is prompt

    def test_fallback_and_plain_strings_are_not_linked(self):
        assert base.linkable_prompt(None) is None
        assert base.linkable_prompt(SimpleNamespace(version=1, is_fallback=True)) is None
        assert base.linkable_prompt("a template string") is None


class _Obs:
    def __init__(self, log, kwargs):
        self.log, self.kwargs = log, kwargs

    def __enter__(self):
        self.log.append(("enter", self.kwargs))
        return self

    def __exit__(self, *a):
        return False

    def update(self, **kw):
        self.log.append(("update", kw))


class _FakeClient:
    def __init__(self):
        self.log = []

    def start_as_current_observation(self, **kwargs):
        return _Obs(self.log, kwargs)


@pytest.fixture
def fake_client(monkeypatch):
    client = _FakeClient()
    monkeypatch.setattr(base, "get_client", lambda: client)
    return client


class TestObservationTypes:
    def test_span_and_tool_accept_a_type(self, fake_client):
        with base.traced_span("retrieve-evidence", as_type="retriever"):
            pass
        with base.traced_tool("compliance-self-check", as_type="guardrail"):
            pass
        with base.traced_tool("calculate"):
            pass
        types = [kw["as_type"] for ev, kw in fake_client.log if ev == "enter"]
        assert types == ["retriever", "guardrail", "tool"]

    def test_agent_wrapper_records_input_and_output(self, fake_client):
        task = base.as_agent_task(lambda *, item, **kw: {"answer": "ok"}, "advisory-draft")
        out = task(item={"input": {"question": "Write an update"}})
        assert out == {"answer": "ok"}
        enter = fake_client.log[0]
        assert enter == ("enter", {"as_type": "agent", "name": "usecase:advisory-draft",
                                   "input": "Write an update"})
        assert ("update", {"output": {"answer": "ok"}}) in fake_client.log


class TestGenerationPromptLink:
    def _patch_anthropic(self, monkeypatch):
        resp = SimpleNamespace(content=[SimpleNamespace(text="hi")],
                               usage=SimpleNamespace(input_tokens=3, output_tokens=1))
        calls = []

        class _Messages:
            def create(self, **kw):
                calls.append(kw)
                return resp

        monkeypatch.setattr(base, "_client", lambda: SimpleNamespace(messages=_Messages()))
        return calls

    def test_managed_prompt_is_passed_and_system_optional(self, fake_client, monkeypatch):
        calls = self._patch_anthropic(monkeypatch)
        prompt = SimpleNamespace(version=2, is_fallback=False)
        assert base.traced_generation(name="g", system=None, user="u",
                                      model="claude-x", prompt=prompt) == "hi"
        enter = next(kw for ev, kw in fake_client.log if ev == "enter")
        assert enter["prompt"] is prompt and enter["as_type"] == "generation"
        assert "system" not in calls[0]

    def test_fallback_prompt_creates_no_link(self, fake_client, monkeypatch):
        self._patch_anthropic(monkeypatch)
        base.traced_generation(name="g", system="s", user="u", model="claude-x",
                               prompt=SimpleNamespace(version=1, is_fallback=True))
        enter = next(kw for ev, kw in fake_client.log if ev == "enter")
        assert enter["prompt"] is None


class TestModelGatePromptRef:
    def test_compiled_text_comes_with_its_prompt(self, monkeypatch):
        import run_certification as rc
        prompt = SimpleNamespace(compile=lambda **kw: f"Q:{kw['text']}", version=1)
        monkeypatch.setattr(rc, "_get_prompt_template", lambda name, fb: prompt)
        text, ref = rc._build_prompt_with_ref({"text": "Profit rose."})
        assert text == "Q:Profit rose." and ref is prompt
        assert rc._build_prompt({"text": "Profit rose."}) == "Q:Profit rose."

    def test_unavailable_prompt_falls_back_without_a_ref(self, monkeypatch):
        import run_certification as rc
        monkeypatch.setattr(rc, "_get_prompt_template", lambda name, fb: None)
        text, ref = rc._build_prompt_with_ref({"text": "Profit rose."})
        assert "Profit rose." in text and ref is None


class TestBooleanScores:
    """Pass/fail scores are BOOLEAN; values stay 1.0/0.0 so averages are unchanged."""

    def test_binary_item_checks(self):
        from evaluators import regulatory_compliance_evaluator, sentiment_evaluator
        ev = regulatory_compliance_evaluator(output="These are guaranteed returns.")
        assert ev.data_type == "BOOLEAN" and ev.value == 0.0
        ev = sentiment_evaluator(output="positive", expected_output={"sentiment": "positive"})
        assert ev.data_type == "BOOLEAN" and ev.value == 1.0

    def test_gate_verdict_is_boolean_and_averages_numeric(self):
        from evaluators import (average_score_evaluator, response_completeness_evaluator,
                                usecase_certification_gate)
        item = SimpleNamespace(evaluations=[
            SimpleNamespace(name="regulatory_compliance", value=1.0)])
        verdict = usecase_certification_gate({"regulatory_compliance": 1.0})(
            item_results=[item])
        assert verdict.data_type == "BOOLEAN" and verdict.value == 1.0
        avg = average_score_evaluator("regulatory_compliance")(item_results=[item])
        assert avg.data_type in (None, "NUMERIC")  # None = server infers NUMERIC
        assert response_completeness_evaluator(output="x" * 400).data_type in (None, "NUMERIC")

    def test_score_configs_match_the_evaluators(self):
        from setup_score_configs import SCORE_CONFIGS, plan_config_changes
        types = {c["name"]: c["dataType"] for c in SCORE_CONFIGS}
        for name in ("certification_result", "regulatory_compliance", "tool_use_correctness",
                     "numerical_accuracy", "exact_match", "sentiment_accuracy"):
            assert types[name] == "BOOLEAN"
            assert "minValue" not in next(c for c in SCORE_CONFIGS if c["name"] == name)
        for name in ("groundedness", "completeness", "avg_groundedness"):
            assert types[name] == "NUMERIC"
        plan = plan_config_changes(
            {"certification_result": {"id": "c1", "dataType": "NUMERIC"},
             "groundedness": {"id": "g1", "dataType": "NUMERIC"}},
            [c for c in SCORE_CONFIGS
             if c["name"] in ("certification_result", "groundedness", "exact_match")])
        assert [(a, c["name"]) for a, c, _ in plan] == [
            ("create", "exact_match"), ("skip", "groundedness"),
            ("migrate", "certification_result")]
