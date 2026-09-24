"""Offline unit tests for cicd/thresholds.json — the quality bar as code.

cicd/thresholds.json is the single source of truth for every deployment gate.
These tests guard the three ways that source of truth can quietly stop being one:

  1. The file and the agent registry diverge (a bar edited in one place only).
  2. A gated dimension has no evaluator producing it, so the run-level average is
     0.0 and the gate can never pass.
  3. An entry goes missing and the gate degrades to `all([]) is True` — certifying
     everything as PASSED.

No network and no credentials: importing `agents` only registers the agents, and
every assertion is over in-memory dicts and one JSON file.
"""
import json
from pathlib import Path

import pytest

import cert_common
from agents import AGENT_REGISTRY
from evaluators import usecase_certification_gate

THRESHOLDS_PATH = Path(__file__).resolve().parent.parent / "cicd" / "thresholds.json"

# Dimension -> the item evaluator expected to produce it. Keeps a gated bar
# honest: a dimension nothing scores averages to 0.0 and fails forever.
_EVALUATOR_FOR_DIMENSION = {
    "numerical_accuracy": "numerical_accuracy_evaluator",
    "sentiment_accuracy": "sentiment_evaluator",
    "groundedness": "groundedness_evaluator",
    "regulatory_compliance": "regulatory_compliance_evaluator",
    "completeness": "response_completeness_evaluator",
    "tool_use_correctness": "tool_use_correctness_evaluator",
}


class TestFileIsWellFormed:
    def test_parses_and_has_both_gate_families(self):
        raw = json.loads(THRESHOLDS_PATH.read_text())
        assert "agent_gates" in raw and "model_gate" in raw

    def test_commentary_is_stripped_by_the_loader(self):
        raw = json.loads(THRESHOLDS_PATH.read_text())
        assert any(k.startswith("_") for k in raw), (
            "the rationale comment is the point of this file — do not delete it")
        assert not any(k.startswith("_")
                       for k in cert_common.load_thresholds(THRESHOLDS_PATH))

    def test_every_threshold_is_a_fraction(self):
        gates = cert_common.load_thresholds(THRESHOLDS_PATH)["agent_gates"]
        for use_case, dims in gates.items():
            for name, value in dims.items():
                assert 0.0 < float(value) <= 1.0, f"{use_case}.{name} = {value}"

    def test_model_gate_threshold_matches_the_file(self):
        raw = json.loads(THRESHOLDS_PATH.read_text())
        assert (cert_common.model_gate_threshold(path=THRESHOLDS_PATH) ==
                pytest.approx(raw["model_gate"]["default_threshold"]))


class TestNoDriftFromTheRegistry:
    """The file is the source of truth; the registry must reflect it exactly."""

    def test_every_registered_agent_matches_the_file(self):
        gates = cert_common.load_thresholds(THRESHOLDS_PATH)["agent_gates"]
        for use_case, entry in AGENT_REGISTRY.items():
            expected = {k: float(v) for k, v in gates[use_case].items()}
            assert entry["gate_thresholds"] == expected, (
                f"'{use_case}' gate drifted from cicd/thresholds.json — the file "
                f"is the source of truth, so fix the file, not the agent module")

    def test_every_file_entry_has_a_registered_agent(self):
        gates = cert_common.load_thresholds(THRESHOLDS_PATH)["agent_gates"]
        orphans = set(gates) - set(AGENT_REGISTRY)
        assert not orphans, (
            f"cicd/thresholds.json defines gates for unregistered use case(s) "
            f"{sorted(orphans)} — a bar nothing reads is not a gate")


class TestEveryGatedDimensionIsMeasured:
    def test_gated_dimensions_have_a_matching_item_evaluator(self):
        for use_case, entry in AGENT_REGISTRY.items():
            names = {getattr(ev, "__name__", "") for ev in entry["item_evaluators"]}
            for dim in entry["gate_thresholds"]:
                expected = _EVALUATOR_FOR_DIMENSION.get(dim)
                assert expected, (
                    f"'{use_case}' gates on unknown dimension '{dim}' — add it to "
                    f"_EVALUATOR_FOR_DIMENSION so coverage stays checkable")
                assert expected in names, (
                    f"'{use_case}' gates on '{dim}' but registers no "
                    f"{expected} — that dimension averages 0.0 and can never pass")


class TestMissingEntryIsFatalNotPermissive:
    """An empty gate is `all([])`, i.e. PASSED. The loader must refuse instead."""

    def test_empty_thresholds_would_certify_everything(self):
        # Documents the hazard the loader guards: this is why a missing entry raises.
        gate = usecase_certification_gate({})
        assert gate(item_results=[]).value == 1.0

    def test_unknown_use_case_raises(self):
        with pytest.raises(KeyError, match="No gate thresholds"):
            cert_common.agent_gate_thresholds("not-a-use-case",
                                              path=THRESHOLDS_PATH)

    def test_empty_entry_raises(self, tmp_path):
        stub = tmp_path / "thresholds.json"
        stub.write_text(json.dumps({"agent_gates": {"10k-analyst": {}}}))
        with pytest.raises(KeyError, match="No gate thresholds"):
            cert_common.agent_gate_thresholds("10k-analyst", path=stub)

    def test_missing_file_raises_with_recovery_advice(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="quality bar"):
            cert_common.agent_gate_thresholds("10k-analyst",
                                              path=tmp_path / "absent.json")

    def test_missing_model_gate_raises(self, tmp_path):
        stub = tmp_path / "thresholds.json"
        stub.write_text(json.dumps({"agent_gates": {}}))
        with pytest.raises(KeyError, match="default_threshold"):
            cert_common.model_gate_threshold(path=stub)
