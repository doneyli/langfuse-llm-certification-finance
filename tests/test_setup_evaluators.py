"""Offline tests for setup_evaluators.py (no network).

The managed code evaluators are generated from evaluators.py; these tests run
the generated source the way Langfuse does (a namespace with the runtime's
Score / EvaluationResult types) and check it agrees with the offline gate.
"""
import ast
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

import evaluators as ev
import setup_evaluators as se


@dataclass
class Score:
    name: str
    value: object
    data_type: str
    comment: str = None
    config_id: str = None
    metadata: dict = None


@dataclass
class EvaluationResult:
    scores: list


def _load(name):
    ns = {"Score": Score, "EvaluationResult": EvaluationResult}
    exec(compile(se.code_evaluator_source(name), f"<{name}>", "exec"), ns)
    return ns["evaluate"]


def _ctx(output):
    return SimpleNamespace(observation=SimpleNamespace(output=output, input=None,
                                                       metadata=None, tool_calls=[]),
                           experiment=None)


TEXTS = [
    "",
    "These are guaranteed returns with no risk.",
    "Revenue grew 12% to $5,634M.",
    "## Summary\n- Revenue grew 12%\n- Margin fell " + "x" * 300,
    "y" * 1200,
]


@pytest.mark.parametrize("text", TEXTS)
@pytest.mark.parametrize("shape", ["str", "agent_dict", "agent_json"])
def test_generated_evaluators_match_the_offline_gate(text, shape):
    output = {"str": text, "agent_dict": {"answer": text, "trajectory": {}},
              "agent_json": json.dumps({"answer": text})}[shape]
    comp = _load("regulatory_compliance")(_ctx(output)).scores[0]
    offline = ev.regulatory_compliance_evaluator(output=text)
    assert comp.data_type == "BOOLEAN"
    assert comp.value is (offline.value == 1.0)
    assert comp.comment == offline.comment

    done = _load("completeness")(_ctx(output)).scores[0]
    offline = ev.response_completeness_evaluator(output=text)
    assert (done.value, done.comment) == (offline.value, offline.comment)


def test_source_is_stdlib_only_and_carries_the_phrase_list():
    for name in ("regulatory_compliance", "completeness"):
        tree = ast.parse(se.code_evaluator_source(name))
        imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                   for a in n.names}
        imports |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert imports <= {"json"}
    assert json.dumps(ev.PROHIBITED_PHRASES) in se.code_evaluator_source(
        "regulatory_compliance")


def test_payload_shapes():
    evs = {p["name"]: p for p in se.evaluator_payloads()}
    assert evs["regulatory_compliance"]["type"] == "code"
    assert evs["regulatory_compliance"]["sourceCodeLanguage"] == "PYTHON"
    judge = evs["groundedness"]
    assert judge["type"] == "llm_as_judge" and judge["modelConfig"] is None
    assert judge["outputDefinition"]["dataType"] == "NUMERIC"
    assert {m["variable"] for m in judge["variableMapping"]} == {"input", "output"}
    assert "{{input}}" in judge["prompt"] and "{{output}}" in judge["prompt"]

    rules = se.rule_payloads({n: f"id-{n}" for n in evs}, environment="production",
                             enable=False, judge_sampling=0.2)
    assert all(r["enabled"] is False for r in rules)
    code_rule, judge_rule = rules
    assert code_rule["sampling"] == 1.0 and judge_rule["sampling"] == 0.2
    assert {"type": "boolean", "column": "isRootObservation", "operator": "=",
            "value": True} in code_rule["filter"]
    assert [a["evaluatorId"] for a in judge_rule["evaluatorAssignments"]] == [
        "id-groundedness"]
