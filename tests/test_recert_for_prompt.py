"""Offline unit tests for scripts/recert_for_prompt.py (loop edge B routing).

Verifies the pure prompt-name -> certification-target mapping. No network, no
subprocess: we only exercise resolve_recert_plan(), never main().
"""
import importlib.util
import sys
from pathlib import Path

import pytest

# scripts/ is not an importable package, so load the module by file path. The
# module must be registered in sys.modules before exec_module so its dataclass's
# (string) annotations resolve under `from __future__ import annotations`.
_MOD_PATH = Path(__file__).resolve().parent.parent / "scripts" / "recert_for_prompt.py"
_spec = importlib.util.spec_from_file_location("recert_for_prompt", _MOD_PATH)
recert = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = recert
_spec.loader.exec_module(recert)


class TestUseCasePrompts:
    @pytest.mark.parametrize("prompt_name,use_case,dataset", [
        ("usecase-10k-analyst-compose", "10k-analyst", "certification/financebench-sample"),
        ("usecase-sentiment-classify", "sentiment-triage", "certification/fpb-sample"),
        ("usecase-advisory-analyze", "advisory-draft", "certification/advisory-adversarial"),
        ("usecase-advisory-draft", "advisory-draft", "certification/advisory-adversarial"),
    ])
    def test_maps_to_usecase_recert(self, prompt_name, use_case, dataset):
        jobs = recert.resolve_recert_plan(prompt_name)
        assert len(jobs) == 1
        argv = jobs[0].argv
        assert "run_usecase_certification.py" in argv
        assert argv[argv.index("--use-case") + 1] == use_case
        assert argv[argv.index("--dataset") + 1] == dataset
        assert "--ci" in argv               # a failing gate must fail the workflow
        assert "--model" not in argv        # no override unless requested


class TestModelCertPrompts:
    @pytest.mark.parametrize("prompt_name,dataset", [
        ("financial-qa", "certification/financebench-sample"),
        ("financial-sentiment", "certification/fpb-sample"),
    ])
    def test_maps_to_model_cert(self, prompt_name, dataset):
        jobs = recert.resolve_recert_plan(prompt_name)
        assert len(jobs) == 1
        argv = jobs[0].argv
        assert "run_certification.py" in argv
        assert "run_usecase_certification.py" not in argv
        assert argv[argv.index("--dataset") + 1] == dataset
        assert "--ci" in argv


class TestModelOverrideAndUnknown:
    def test_model_override_is_appended(self):
        jobs = recert.resolve_recert_plan("usecase-advisory-draft", model="claude-haiku-4-5-20251001")
        argv = jobs[0].argv
        assert argv[argv.index("--model") + 1] == "claude-haiku-4-5-20251001"

    def test_unknown_prompt_maps_to_nothing(self):
        # An unrelated prompt name is a no-op (empty plan), not an error.
        assert recert.resolve_recert_plan("some-unrelated-prompt") == []
        assert recert.resolve_recert_plan("") == []


class TestJobSummary:
    """The skip case exits 0 and renders as a green check, so the summary is the
    only thing that distinguishes 'nothing was gated' from 'the gate passed'."""

    def test_skip_states_it_makes_no_claim(self):
        report = recert.render_summary("some-unrelated-prompt", None, [])
        assert "Skipped" in report
        assert "no claim" in report
        assert "PASSED" not in report

    def test_pass_verdict_lists_each_target(self):
        jobs = recert.resolve_recert_plan("usecase-advisory-draft")
        report = recert.render_summary("usecase-advisory-draft", None, jobs, [0])
        assert "✅ PASSED" in report
        assert jobs[0].label in report

    def test_fail_verdict_names_the_rollback(self):
        jobs = recert.resolve_recert_plan("usecase-advisory-draft")
        report = recert.render_summary("usecase-advisory-draft", None, jobs, [1])
        assert "❌ FAILED (exit 1)" in report
        assert "must not stay on" in report

    def test_same_label_jobs_do_not_collapse(self):
        # Verdicts are positional, so a duplicated label cannot hide a failure.
        jobs = [recert.RecertJob(label="dup", argv=[]),
                recert.RecertJob(label="dup", argv=[])]
        report = recert.render_summary("p", None, jobs, [0, 1])
        assert "✅ PASSED" in report and "❌ FAILED (exit 1)" in report
        assert "must not stay on" in report

    def test_write_summary_is_a_noop_outside_actions(self, monkeypatch):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        recert.write_summary("ignored")  # must not raise

    def test_write_summary_appends_in_actions(self, monkeypatch, tmp_path):
        path = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(path))
        recert.write_summary("first\n")
        recert.write_summary("second\n")
        assert path.read_text() == "first\nsecond\n"

    def test_unwritable_summary_does_not_fail_the_gate(self, monkeypatch, tmp_path):
        # A broken summary path is a reporting problem, never a gate verdict.
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "no" / "such" / "f"))
        recert.write_summary("ignored")


class TestNoDriftFromRegisteredPrompts:
    def test_every_registered_certification_prompt_is_routed(self):
        """Guard: if a new managed cert prompt is added to setup_prompts.py, it
        must also get a recert route here (else a promotion would silently skip
        re-certification)."""
        import setup_prompts
        names = {p["name"] for p in setup_prompts.PROMPTS}
        routed = set(recert._USECASE_BY_PROMPT) | set(recert._MODELCERT_BY_PROMPT)
        # Every registered prompt name we know about today must be routed.
        unrouted = names - routed
        assert not unrouted, (
            f"managed prompt(s) {sorted(unrouted)} have no recert route in "
            f"scripts/recert_for_prompt.py — add them to the mapping")
