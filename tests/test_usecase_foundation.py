"""
Use-Case Certification Foundation — unit tests (offline, no network).

Covers the shared foundation from GitHub issue #8:
  - usecase_certification_gate     (multi-dimensional PASS/FAIL)
  - tool_use_correctness_evaluator (trajectory ground truth)
  - _answer_text guard             (evaluators accept dict OR string)
  - agent registry                 (register/lookup)
  - import smoke tests             (both runners + cert_common import cleanly)

These run without Langfuse credentials or any LLM calls.
"""

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from evaluators import (
    usecase_certification_gate,
    tool_use_correctness_evaluator,
    numerical_accuracy_evaluator,
    regulatory_compliance_evaluator,
)


# --------------- Test doubles mirroring the Langfuse SDK shapes ---------------

@dataclass
class _Eval:
    name: str
    value: float
    comment: str = ""


@dataclass
class _ItemResult:
    evaluations: list


def _run(items):
    """items: list[dict[name->value]] -> list[_ItemResult]."""
    return [
        _ItemResult([_Eval(n, v) for n, v in scores.items()])
        for scores in items
    ]


# --------------- usecase_certification_gate ---------------

class TestUsecaseCertificationGate:
    THRESHOLDS = {
        "numerical_accuracy": 0.85,
        "groundedness": 0.80,
        "regulatory_compliance": 1.0,
        "tool_use_correctness": 0.90,
    }

    def test_passes_when_all_dimensions_clear(self):
        gate = usecase_certification_gate(self.THRESHOLDS)
        items = _run([
            {"numerical_accuracy": 1.0, "groundedness": 0.9,
             "regulatory_compliance": 1.0, "tool_use_correctness": 1.0},
            {"numerical_accuracy": 1.0, "groundedness": 0.85,
             "regulatory_compliance": 1.0, "tool_use_correctness": 1.0},
        ])
        ev = gate(item_results=items)
        assert ev.name == "certification_result"
        assert ev.value == 1.0
        assert ev.comment.startswith("PASSED")

    def test_fails_when_a_single_dimension_is_below(self):
        gate = usecase_certification_gate(self.THRESHOLDS)
        # numerical_accuracy averages 0.5 -> below 0.85, everything else perfect
        items = _run([
            {"numerical_accuracy": 1.0, "groundedness": 0.9,
             "regulatory_compliance": 1.0, "tool_use_correctness": 1.0},
            {"numerical_accuracy": 0.0, "groundedness": 0.9,
             "regulatory_compliance": 1.0, "tool_use_correctness": 1.0},
        ])
        ev = gate(item_results=items)
        assert ev.value == 0.0
        assert ev.comment.startswith("FAILED")
        assert "numerical_accuracy" in ev.comment

    def test_compliance_is_a_hard_gate(self):
        gate = usecase_certification_gate(self.THRESHOLDS)
        # one prohibited-phrase violation drops avg compliance below 1.0
        items = _run([
            {"numerical_accuracy": 1.0, "groundedness": 0.9,
             "regulatory_compliance": 1.0, "tool_use_correctness": 1.0},
            {"numerical_accuracy": 1.0, "groundedness": 0.9,
             "regulatory_compliance": 0.0, "tool_use_correctness": 1.0},
        ])
        ev = gate(item_results=items)
        assert ev.value == 0.0

    def test_missing_dimension_fails(self):
        # A dimension with no recorded scores averages to 0.0 -> cannot certify.
        gate = usecase_certification_gate(self.THRESHOLDS)
        items = _run([
            {"numerical_accuracy": 1.0, "groundedness": 0.9,
             "regulatory_compliance": 1.0},  # tool_use_correctness absent
        ])
        ev = gate(item_results=items)
        assert ev.value == 0.0
        assert "tool_use_correctness" in ev.comment


# --------------- tool_use_correctness_evaluator ---------------

class TestToolUseCorrectness:
    def _out(self, qtype, tools):
        return {"answer": "x", "trajectory": {"question_type": qtype, "tools_used": tools}}

    def test_numerical_without_calculator_fails(self):
        ev = tool_use_correctness_evaluator(
            output=self._out("Numerical reasoning", []),
            metadata={"question_reasoning": "Numerical reasoning"},
        )
        assert ev.value == 0.0
        assert "calculate" in ev.comment

    def test_numerical_with_calculator_passes(self):
        ev = tool_use_correctness_evaluator(
            output=self._out("Numerical reasoning", ["calculate"]),
            metadata={"question_reasoning": "Numerical reasoning"},
        )
        assert ev.value == 1.0

    def test_logical_reasoning_does_not_mandate_calculator(self):
        # "Logical reasoning (based on numerical reasoning)" questions are yes/no
        # judgments over several ratios; they do not reduce to one sanctioned
        # calculation, so the calculator is not required (only pure "numerical").
        ev = tool_use_correctness_evaluator(
            output=self._out("Logical reasoning (based on numerical reasoning)", []),
            metadata={"question_reasoning": "Logical reasoning (based on numerical reasoning)"},
        )
        assert ev.value == 1.0

    def test_information_extraction_needs_no_tool(self):
        ev = tool_use_correctness_evaluator(
            output=self._out("Information extraction", []),
            metadata={"question_reasoning": "Information extraction"},
        )
        assert ev.value == 1.0

    def test_sentiment_requires_route_tool(self):
        ev = tool_use_correctness_evaluator(
            output=self._out("sentiment", []), metadata=None,
        )
        assert ev.value == 0.0
        assert "route" in ev.comment

    def test_question_type_from_trajectory_when_no_metadata(self):
        ev = tool_use_correctness_evaluator(
            output=self._out("advisory", ["compliance-self-check"]), metadata={},
        )
        assert ev.value == 1.0

    def test_non_agent_output_is_skipped(self):
        # model-cert runs pass a bare string -> evaluator returns None (skip)
        assert tool_use_correctness_evaluator(output="some answer", metadata={}) is None


# --------------- _answer_text guard (backward compatibility) ---------------

class TestAnswerTextGuard:
    EXPECTED = {"answer": "$1,577.00"}

    def test_numerical_accuracy_with_string_output(self):
        ev = numerical_accuracy_evaluator(
            output="The capex was $1,577 million.", expected_output=self.EXPECTED,
        )
        assert ev.value == 1.0

    def test_numerical_accuracy_with_agent_dict_output(self):
        ev = numerical_accuracy_evaluator(
            output={"answer": "The capex was $1,577 million.", "trajectory": {}},
            expected_output=self.EXPECTED,
        )
        assert ev.value == 1.0

    def test_compliance_reads_answer_from_dict(self):
        ev = regulatory_compliance_evaluator(
            output={"answer": "This is a guaranteed returns scheme.", "trajectory": {}},
        )
        assert ev.value == 0.0  # prohibited phrase detected inside the dict's answer


# --------------- Agent registry ---------------

class TestAgentRegistry:
    def test_register_and_lookup(self):
        from agents.base import AGENT_REGISTRY, register_agent

        def _factory(*, model, **opts):
            def task(*, item, **kwargs):
                return {"answer": "ok", "trajectory": {}}
            return task

        register_agent(
            "unit-test-agent",
            fn=_factory,
            gate_thresholds={"numerical_accuracy": 0.85},
            item_evaluators=[numerical_accuracy_evaluator],
            dataset_hint="financebench",
            description="unit test",
        )
        entry = AGENT_REGISTRY["unit-test-agent"]
        assert entry["gate_thresholds"]["numerical_accuracy"] == 0.85
        assert entry["dataset_hint"] == "financebench"
        task = entry["fn"](model="claude-sonnet-4-6")
        assert task(item={"input": {}})["answer"] == "ok"
        del AGENT_REGISTRY["unit-test-agent"]


# --------------- Import smoke tests ---------------

class TestImports:
    def test_cert_common_imports(self):
        import cert_common
        assert hasattr(cert_common, "persist_run_evaluations")
        assert hasattr(cert_common, "queue_failed_items")
        assert hasattr(cert_common, "get_managed_prompt")

    def test_runners_import(self):
        # Both runners must import cleanly (catches syntax/import regressions).
        import run_certification  # noqa: F401
        import run_usecase_certification  # noqa: F401

    def test_agents_package_tolerates_missing_agent_modules(self):
        # Foundation must work before the agent modules land (#9/#10/#11).
        import agents
        assert isinstance(agents.AGENT_REGISTRY, dict)
