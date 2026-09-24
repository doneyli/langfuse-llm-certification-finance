"""
Reading the gate a run recorded — unit tests (offline, no network).

The portal and the evidence pack (export_results.py) both report the bar a run
was judged against, and three producers record it in two shapes:

  * run_certification.py         -> metadata.threshold + metadata.gate
  * tests/test_certification.py  -> metadata.threshold + metadata.gate
  * run_usecase_certification.py -> metadata.gate_thresholds (one bar per
                                    dimension, all of which must clear)

cert_common.recorded_gate() is the one rule both readers share. These tests pin
it so no reader can again fall back to a hardcoded 85% the gate never enforced,
or apply a model gate's single bar to evaluators that bar never judged.

They run without Langfuse credentials or any LLM calls.
"""

import pytest

from agents.advisory_draft import GATE_ADVISORY_DRAFT
from cert_common import MODEL_GATE_SCORES, describe_gate, recorded_gate
from export_results import format_markdown
from portal.langfuse_client import (
    replay_gate,
    resolve_run_thresholds,
    summarize_run,
)


# What run_usecase_certification.py records for the advisory agent: the
# registry's own gate dict. Built from the registry rather than copied, so a
# bar edited in cicd/thresholds.json flows through instead of failing here.
ADVISORY_META = {
    "model": "usecase:advisory-draft",
    "use_case": "advisory-draft",
    "gate_thresholds": dict(GATE_ADVISORY_DRAFT),
}

MODEL_META = {"model": "claude-sonnet-4-6", "threshold": 0.85,
              "gate": "numerical_accuracy"}

# A model run recorded before run_certification.py wrote `gate`.
LEGACY_MODEL_META = {"model": "claude-sonnet-4-6", "threshold": 0.85}


class TestRecordedGate:
    def test_agent_gate_is_returned_whole(self):
        assert recorded_gate(ADVISORY_META) == {
            k: float(v) for k, v in GATE_ADVISORY_DRAFT.items()}

    def test_json_round_tripped_bars_are_coerced_to_float(self):
        # Langfuse's JS API has no int/float distinction, so 1.00 comes back as
        # int 1. A literal here on purpose: this pins coercion, not the registry.
        gate = recorded_gate({"gate_thresholds": {"regulatory_compliance": 1,
                                                  "groundedness": 0.8}})
        assert gate == {"regulatory_compliance": 1.0, "groundedness": 0.8}
        assert all(isinstance(v, float) for v in gate.values())

    def test_model_gate_names_its_one_judged_score(self):
        assert recorded_gate(MODEL_META) == {"numerical_accuracy": 0.85}

    def test_live_gate_names_its_judged_score(self):
        meta = {"model": "ci-claude-haiku-4-5-20251001", "threshold": 1.0,
                "gate": "regulatory_compliance"}
        assert recorded_gate(meta) == {"regulatory_compliance": 1.0}

    @pytest.mark.parametrize("names,judged", [
        ({"numerical_accuracy", "exact_match", "groundedness"}, "numerical_accuracy"),
        ({"sentiment_accuracy", "regulatory_compliance"}, "sentiment_accuracy"),
    ])
    def test_legacy_model_run_infers_the_judged_score(self, names, judged):
        assert recorded_gate(LEGACY_MODEL_META, names) == {judged: 0.85}

    def test_legacy_run_that_gated_nothing_has_no_bar(self):
        # `--evaluators compliance` runs no gate at all: its threshold judged
        # nothing, so reporting it as a bar would be inventing one.
        assert recorded_gate(LEGACY_MODEL_META, {"regulatory_compliance"}) is None

    def test_no_recorded_threshold_is_not_invented(self):
        assert recorded_gate({"model": "legacy-ci-run"}) is None
        assert recorded_gate(None) is None

    @pytest.mark.parametrize("raw", [{}, "0.8", {"groundedness": None},
                                     {"groundedness": True}])
    def test_agent_gate_with_no_usable_bars_never_falls_back_to_a_scalar(self, raw):
        # An empty agent gate certifies everything, so no bar was enforced —
        # a stray scalar must not be presented as one.
        assert recorded_gate({"gate_thresholds": raw, "threshold": 0.9}) is None

    def test_describe_gate(self):
        assert describe_gate({"numerical_accuracy": 0.85}) == "numerical_accuracy ≥ 85%"
        assert describe_gate({"groundedness": 0.8, "completeness": 0.7}) == (
            "all must clear: completeness ≥ 70% · groundedness ≥ 80%")
        assert "no bar recorded" in describe_gate(None)


class TestModelGateInferenceMatchesTheRunner:
    """Legacy inference is only sound while select_evaluators() keeps gating
    exactly one MODEL_GATE_SCORES score, and only when it registers that
    score's evaluator. If the runner changes, this fails before the portal lies."""

    @pytest.mark.parametrize("mode", ["all", "accuracy", "compliance", "sentiment"])
    @pytest.mark.parametrize("dataset", ["certification/financebench-sample",
                                         "certification/fpb-sample"])
    def test_judged_score_is_the_one_gate_evaluator_it_registers(self, mode, dataset):
        from evaluators import numerical_accuracy_evaluator, sentiment_evaluator
        from run_certification import select_evaluators

        items, _, primary = select_evaluators(mode, dataset, 0.85)
        registered = {
            name for name, ev in (("numerical_accuracy", numerical_accuracy_evaluator),
                                  ("sentiment_accuracy", sentiment_evaluator))
            if ev in items
        }
        assert primary is None or primary in MODEL_GATE_SCORES
        assert registered == ({primary} if primary else set())


class TestResolveRunThresholds:
    def test_model_bar_applies_to_its_judged_score(self):
        assert resolve_run_thresholds(MODEL_META, "avg_numerical_accuracy") == (0.85, None)

    def test_model_bar_does_not_apply_to_other_scores(self):
        assert resolve_run_thresholds(MODEL_META, "avg_exact_match") == (None, None)

    def test_model_run_without_a_primary_score_reports_its_one_bar(self):
        assert resolve_run_thresholds(MODEL_META) == (0.85, None)

    def test_agent_primary_score_gets_its_own_dimension_bar(self):
        threshold, gate = resolve_run_thresholds(ADVISORY_META, "avg_groundedness")
        assert threshold == float(GATE_ADVISORY_DRAFT["groundedness"])
        assert gate == recorded_gate(ADVISORY_META)

    def test_agent_dimension_outside_the_gate_has_no_bar_even_with_a_scalar(self):
        meta = {**ADVISORY_META, "threshold": 0.9}
        threshold, gate = resolve_run_thresholds(meta, "avg_exact_match")
        assert threshold is None
        assert gate is not None

    def test_agent_gate_has_no_single_bar(self):
        assert resolve_run_thresholds(ADVISORY_META)[0] is None


class TestReplayGate:
    """Fallback verdict for runs with no persisted certification_result."""

    GATE = {"groundedness": 0.8, "regulatory_compliance": 1.0}

    def test_passes_when_every_bar_clears(self):
        assert replay_gate(self.GATE, {"groundedness": 0.88,
                                       "regulatory_compliance": 1.0}) == "PASSED"

    def test_hard_dimension_below_its_own_bar_fails(self):
        assert replay_gate(self.GATE, {"groundedness": 0.88,
                                       "regulatory_compliance": 0.9}) == "FAILED"

    def test_missing_dimension_cannot_certify(self):
        assert replay_gate(self.GATE, {"groundedness": 0.9}) == "FAILED"

    def test_compares_the_unrounded_mean(self):
        # 0.9996 rounds to 1.0 for display but misses a 1.00 bar — as the gate saw it.
        assert replay_gate({"regulatory_compliance": 1.0},
                           {"regulatory_compliance": 0.9996}) == "FAILED"


class TestSummarizeRun:
    def test_model_run_bar_lands_only_on_its_judged_score(self):
        totals = {"numerical_accuracy": [1.0, 1.0, 0.0, 1.0],
                  "exact_match": [0.0, 0.0, 1.0, 0.0],
                  "regulatory_compliance": [1.0, 1.0, 1.0, 1.0]}
        aggs = summarize_run(totals, MODEL_META)["aggregates"]
        assert aggs["numerical_accuracy"]["bar"] == 0.85
        assert aggs["numerical_accuracy"]["cleared"] is False
        # The model gate never judged these, so they carry no bar and no verdict.
        for name in ("exact_match", "regulatory_compliance"):
            assert aggs[name]["bar"] is None
            assert aggs[name]["cleared"] is None

    def test_agent_run_every_dimension_carries_its_own_bar(self):
        totals = {"groundedness": [0.9, 0.86], "regulatory_compliance": [1.0, 1.0],
                  "completeness": [0.8, 0.7], "tool_use_correctness": [1.0, 1.0],
                  "exact_match": [0.0, 1.0]}
        summary = summarize_run(totals, ADVISORY_META)
        aggs = summary["aggregates"]
        for dim, bar in GATE_ADVISORY_DRAFT.items():
            assert aggs[dim]["bar"] == float(bar)
            assert aggs[dim]["cleared"] is True
        assert aggs["exact_match"]["bar"] is None
        assert summary["threshold"] is None
        assert summary["gate_thresholds"] == recorded_gate(ADVISORY_META)

    def test_cleared_uses_the_unrounded_mean(self):
        totals = {"regulatory_compliance": [1.0] * 2499 + [0.0]}  # mean 0.9996
        aggs = summarize_run(totals, {"gate_thresholds":
                                      {"regulatory_compliance": 1.0}})["aggregates"]
        assert aggs["regulatory_compliance"]["mean"] == 1.0   # display rounding
        assert aggs["regulatory_compliance"]["cleared"] is False

    def test_persisted_gate_score_wins(self):
        totals = {"numerical_accuracy": [0.0, 0.0]}
        assert summarize_run(totals, MODEL_META, cert_value=1.0)["status"] == "PASSED"

    def test_legacy_run_is_replayed_against_its_recorded_bar(self):
        totals = {"numerical_accuracy": [1.0, 1.0, 1.0, 0.0], "exact_match": [0.0] * 4}
        # 0.75 < 0.85 on the judged score; exact_match is not part of the verdict.
        assert summarize_run(totals, LEGACY_MODEL_META)["status"] == "FAILED"

    def test_run_that_recorded_no_bar_is_unknown(self):
        totals = {"regulatory_compliance": [1.0, 1.0]}
        assert summarize_run(totals, LEGACY_MODEL_META)["status"] == "UNKNOWN"


class TestEvidencePack:
    """export_results.py must report the bars the run was actually judged by."""

    @staticmethod
    def _pack(meta, aggregates):
        return format_markdown({
            "dataset": "certification/advisory-adversarial",
            "run_name": "r1", "run_metadata": meta,
            "exported_at": "2026-09-23T00:00:00", "total_items": 2,
            "aggregates": {name: {"mean": 1.0, "min": 1.0, "max": 1.0,
                                  "count": 2, "pass_rate": 1.0}
                           for name in aggregates},
            "items": [],
        })

    def test_agent_run_lists_every_dimension_and_no_invented_bar(self):
        report = self._pack(ADVISORY_META, GATE_ADVISORY_DRAFT)
        gate_row = next(l for l in report.splitlines() if l.startswith("| **Gate**"))
        assert "all must clear" in gate_row
        for dim, bar in GATE_ADVISORY_DRAFT.items():
            assert f"{dim} ≥ {float(bar):.0%}" in gate_row
        assert "85%" not in gate_row

    def test_model_run_names_its_judged_score(self):
        report = self._pack(MODEL_META, ["numerical_accuracy", "exact_match"])
        assert "| **Gate** | numerical_accuracy ≥ 85% |" in report

    def test_run_with_no_bar_says_so(self):
        report = self._pack({"model": "legacy"}, ["regulatory_compliance"])
        assert "no bar recorded" in report
