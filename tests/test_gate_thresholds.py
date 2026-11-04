"""Offline unit tests for cicd/thresholds.json — the quality bar as code.

cicd/thresholds.json is the single source of truth for every deployment gate.
These tests guard the ways that source of truth can quietly stop being one:

  1. The file and the agent registry diverge (a bar edited in one place only).
  2. A gated dimension has no evaluator producing it, so the run-level average is
     0.0 and the gate can never pass.
  3. An entry goes missing, or every bar in it is advisory, and the gate degrades
     to `all([]) is True` — certifying everything as PASSED.
  4. An advisory bar leaks into the verdict, or a required one leaks out of it.

No network and no credentials: importing `agents` only registers the agents, and
every assertion is over in-memory dicts and one JSON file.
"""
import json
from pathlib import Path
from types import SimpleNamespace

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


def _stub(tmp_path, entry, use_case="10k-analyst"):
    path = tmp_path / "thresholds.json"
    path.write_text(json.dumps({"agent_gates": {use_case: entry}}))
    return path


def _items(*rows):
    """Item results shaped like the SDK's: each has .evaluations[name, value]."""
    return [SimpleNamespace(evaluations=[SimpleNamespace(name=n, value=v)
                                         for n, v in row.items()])
            for row in rows]


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
        for use_case in gates:
            required, advisory = cert_common.agent_gate_bars(
                use_case, path=THRESHOLDS_PATH)
            for name, value in {**required, **advisory}.items():
                assert 0.0 < value <= 1.0, f"{use_case}.{name} = {value}"

    def test_every_agent_has_a_required_bar(self):
        gates = cert_common.load_thresholds(THRESHOLDS_PATH)["agent_gates"]
        for use_case in gates:
            assert cert_common.agent_gate_thresholds(use_case,
                                                     path=THRESHOLDS_PATH)

    def test_model_gate_threshold_matches_the_file(self):
        raw = json.loads(THRESHOLDS_PATH.read_text())
        assert (cert_common.model_gate_threshold(path=THRESHOLDS_PATH) ==
                pytest.approx(raw["model_gate"]["default_threshold"]))


class TestNoDriftFromTheRegistry:
    """The file is the source of truth; the registry must reflect it exactly."""

    def test_every_registered_agent_matches_the_file(self):
        for use_case, entry in AGENT_REGISTRY.items():
            required, advisory = cert_common.agent_gate_bars(
                use_case, path=THRESHOLDS_PATH)
            assert entry["gate_thresholds"] == required, (
                f"'{use_case}' required bars drifted from cicd/thresholds.json — "
                f"the file is the source of truth, so fix the file, not the "
                f"agent module")
            assert entry["advisory_thresholds"] == advisory, (
                f"'{use_case}' advisory bars drifted from cicd/thresholds.json")

    def test_every_file_entry_has_a_registered_agent(self):
        gates = cert_common.load_thresholds(THRESHOLDS_PATH)["agent_gates"]
        orphans = set(gates) - set(AGENT_REGISTRY)
        assert not orphans, (
            f"cicd/thresholds.json defines gates for unregistered use case(s) "
            f"{sorted(orphans)} — a bar nothing reads is not a gate")


class TestEveryGatedDimensionIsMeasured:
    def test_gated_dimensions_have_a_matching_item_evaluator(self):
        # Advisory bars included: an advisory bar nothing scores reports a
        # permanent MISS, which is noise rather than signal.
        for use_case, entry in AGENT_REGISTRY.items():
            names = {getattr(ev, "__name__", "") for ev in entry["item_evaluators"]}
            dims = {**entry["gate_thresholds"], **entry["advisory_thresholds"]}
            for dim in dims:
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
        with pytest.raises(KeyError, match="No gate thresholds"):
            cert_common.agent_gate_thresholds("10k-analyst",
                                              path=_stub(tmp_path, {}))

    def test_all_advisory_entry_raises(self, tmp_path):
        # Advisory bars never block, so an entry with only advisory bars is an
        # empty gate in disguise: it must fail at import exactly like {}.
        stub = _stub(tmp_path, {
            "groundedness": {"threshold": 0.8, "required": False},
            "completeness": {"threshold": 0.7, "required": False},
        })
        for loader in (cert_common.agent_gate_thresholds,
                       cert_common.agent_advisory_thresholds,
                       cert_common.agent_gate_bars):
            with pytest.raises(KeyError, match="No required gate thresholds"):
                loader("10k-analyst", path=stub)

    def test_missing_file_raises_with_recovery_advice(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="quality bar"):
            cert_common.agent_gate_thresholds("10k-analyst",
                                              path=tmp_path / "absent.json")

    def test_missing_model_gate_raises(self, tmp_path):
        stub = tmp_path / "thresholds.json"
        stub.write_text(json.dumps({"agent_gates": {}}))
        with pytest.raises(KeyError, match="default_threshold"):
            cert_common.model_gate_threshold(path=stub)


class TestAdvisoryTierParsing:
    """`{"threshold": x, "required": false}` marks a bar advisory; a bare number
    stays required, so a file written before the tier existed is unchanged."""

    def test_bare_number_is_required(self, tmp_path):
        stub = _stub(tmp_path, {"regulatory_compliance": 1, "groundedness": 0.8})
        assert cert_common.agent_gate_bars("10k-analyst", path=stub) == (
            {"regulatory_compliance": 1.0, "groundedness": 0.8}, {})

    def test_object_form_splits_the_tiers(self, tmp_path):
        stub = _stub(tmp_path, {
            "regulatory_compliance": 1.0,
            "groundedness": {"threshold": 0.8},                    # required by default
            "tool_use_correctness": {"threshold": 0.9, "required": True},
            "completeness": {"threshold": 0.7, "required": False},
        })
        required, advisory = cert_common.agent_gate_bars("10k-analyst", path=stub)
        assert required == {"regulatory_compliance": 1.0, "groundedness": 0.8,
                            "tool_use_correctness": 0.9}
        assert advisory == {"completeness": 0.7}
        assert cert_common.agent_gate_thresholds("10k-analyst", path=stub) == required
        assert cert_common.agent_advisory_thresholds("10k-analyst", path=stub) == advisory

    @pytest.mark.parametrize("bad", [
        {"threshold": 0.7, "requried": False},     # typo must not pass silently
        {"required": False},                        # no threshold
        {"threshold": 0.7, "required": "false"},   # string, not bool
        {"threshold": True},                        # bool is not a number
        {"threshold": "0.7"},                       # string is not a number
        "0.7",
        None,
    ])
    def test_malformed_entry_raises(self, tmp_path, bad):
        stub = _stub(tmp_path, {"regulatory_compliance": 1.0, "completeness": bad})
        with pytest.raises(ValueError, match="agent_gates.10k-analyst.completeness"):
            cert_common.agent_gate_bars("10k-analyst", path=stub)

    def test_shipped_advisory_bar_is_recorded_apart_from_the_gate(self):
        # advisory-draft carries the repo's one advisory bar. It must reach the
        # registry's advisory map and never its gate_thresholds, which is what
        # the runner records as metadata.gate_thresholds and the portal replays
        # as all-must-clear.
        entry = AGENT_REGISTRY["advisory-draft"]
        assert "completeness" in entry["advisory_thresholds"]
        assert "completeness" not in entry["gate_thresholds"]


class TestAdvisoryBarsNeverDecideTheVerdict:
    REQUIRED = {"regulatory_compliance": 1.0, "groundedness": 0.8}
    ADVISORY = {"completeness": 0.7}

    def test_advisory_miss_does_not_fail_the_run(self):
        gate = usecase_certification_gate(self.REQUIRED, advisory=self.ADVISORY)
        ev = gate(item_results=_items(
            {"regulatory_compliance": 1.0, "groundedness": 0.9, "completeness": 0.2},
            {"regulatory_compliance": 1.0, "groundedness": 0.9, "completeness": 0.5},
        ))
        assert ev.value == 1.0
        assert ev.comment.startswith("PASSED")
        assert "advisory, does not block: completeness=35%MISS(>=70%)" in ev.comment

    def test_required_miss_fails_even_when_advisory_is_met(self):
        gate = usecase_certification_gate(self.REQUIRED, advisory=self.ADVISORY)
        ev = gate(item_results=_items(
            {"regulatory_compliance": 0.0, "groundedness": 0.9, "completeness": 1.0},
        ))
        assert ev.value == 0.0
        assert ev.comment.startswith("FAILED")
        assert "regulatory_compliance=0%FAIL" in ev.comment
        assert "completeness=100%MET(>=70%)" in ev.comment

    def test_unmeasured_advisory_bar_is_reported_not_fatal(self):
        gate = usecase_certification_gate(self.REQUIRED, advisory=self.ADVISORY)
        ev = gate(item_results=_items(
            {"regulatory_compliance": 1.0, "groundedness": 0.9},
        ))
        assert ev.value == 1.0
        assert "completeness=0%MISS" in ev.comment

    def test_no_advisory_bars_leaves_the_comment_unchanged(self):
        ev = usecase_certification_gate(self.REQUIRED)(item_results=_items(
            {"regulatory_compliance": 1.0, "groundedness": 0.9},
        ))
        assert "advisory" not in ev.comment

    def test_a_bar_cannot_be_both_required_and_advisory(self):
        with pytest.raises(ValueError, match="both required and advisory"):
            usecase_certification_gate(self.REQUIRED,
                                       advisory={"groundedness": 0.5})
