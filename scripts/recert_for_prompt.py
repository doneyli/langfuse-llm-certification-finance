#!/usr/bin/env python3
"""Re-certify the target(s) affected by a Langfuse prompt promotion.

This closes loop edge B (see docs/ai-engineering-loop.md): when a managed prompt
is promoted in Langfuse, the GitHub integration fires a ``repository_dispatch``
(``event_type=langfuse-prompt-update``) which runs
``.github/workflows/prompt-recert.yml``, which calls this script.

We map the *changed prompt name* to the certification target(s) that consume it
and re-run each with ``--ci`` — so a prompt change that regresses the gate fails
the workflow instead of silently shipping.

Routing is by prompt **name only**. We deliberately do NOT read prompt content
from the dispatch payload: GitHub truncates large ``client_payload`` fields, so
the payload is not authoritative — the re-cert run fetches the live
``production`` prompt from Langfuse itself.

Usage:
    python scripts/recert_for_prompt.py --prompt-name usecase-advisory-draft
    python scripts/recert_for_prompt.py --prompt-name financial-qa --model claude-haiku-4-5-20251001

Exit code: 0 if every mapped re-cert passed (or the prompt maps to nothing),
1 if any mapped re-cert failed its gate.

A verdict is also written to ``$GITHUB_STEP_SUMMARY`` when running in Actions, so
the outcome is readable in the run summary without opening logs. That matters most
for the *skip* case: an unmapped prompt exits 0, which renders as a green check —
indistinguishable from "the gate ran and passed" unless the run says otherwise.
A skip makes no claim about quality, and the summary states so explicitly.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RecertJob:
    """One certification run to execute for a changed prompt."""
    label: str                       # human-readable description for logs
    argv: list[str] = field(default_factory=list)  # command to run (from repo root)


# Use-case (agent) prompts -> (use_case, dataset). A change to any step prompt of
# an agent re-certifies that whole agent (the gate is on the system, not a step).
_USECASE_BY_PROMPT = {
    "usecase-10k-analyst-compose": ("10k-analyst", "certification/financebench-sample"),
    "usecase-sentiment-classify": ("sentiment-triage", "certification/fpb-sample"),
    # advisory-draft certifies on advisory-adversarial, not financebench:
    # FinanceBench items carry question_reasoning="Numerical reasoning", which
    # makes tool_use_correctness demand the calculator tool this agent never
    # uses — its hard tool_use gate (1.00) can therefore never pass there.
    "usecase-advisory-analyze": ("advisory-draft", "certification/advisory-adversarial"),
    "usecase-advisory-draft": ("advisory-draft", "certification/advisory-adversarial"),
}

# Model-certification prompts -> dataset.
_MODELCERT_BY_PROMPT = {
    "financial-qa": "certification/financebench-sample",
    "financial-sentiment": "certification/fpb-sample",
}


def resolve_recert_plan(prompt_name: str, *, model: str | None = None) -> list[RecertJob]:
    """Return the RecertJob(s) to run for a changed prompt name.

    Returns an empty list for a prompt that no certification target consumes
    (e.g. an unrelated experiment prompt) — the caller treats that as a no-op.
    """
    if prompt_name in _USECASE_BY_PROMPT:
        use_case, dataset = _USECASE_BY_PROMPT[prompt_name]
        argv = [sys.executable, "run_usecase_certification.py",
                "--use-case", use_case, "--dataset", dataset, "--ci"]
        if model:
            argv += ["--model", model]
        return [RecertJob(label=f"use-case '{use_case}' on {dataset}", argv=argv)]

    if prompt_name in _MODELCERT_BY_PROMPT:
        dataset = _MODELCERT_BY_PROMPT[prompt_name]
        argv = [sys.executable, "run_certification.py", "--dataset", dataset, "--ci"]
        if model:
            argv += ["--model", model]
        return [RecertJob(label=f"model-cert on {dataset}", argv=argv)]

    return []


def render_summary(prompt_name: str, model: str | None, jobs: list[RecertJob],
                   results: list[int] | None = None) -> str:
    """Markdown verdict for the Actions run summary.

    ``results`` holds one exit code per job, positionally — not keyed by label,
    so two jobs sharing a label cannot collapse a FAILED into a PASSED. Passing
    ``None`` renders the plan only (used for the skip case, where nothing runs).
    """
    lines = ["### Prompt re-certification", "",
             "| field | value |", "|---|---|",
             f"| prompt | `{prompt_name}` |",
             f"| model | `{model or 'default'}` |"]

    if not jobs:
        lines += [
            "", "### ⏭️ Skipped — nothing to re-certify", "",
            f"`{prompt_name}` maps to no certification target in "
            f"`scripts/recert_for_prompt.py`.", "",
            "> This is a deliberate routing decision, not a passing gate. No gate "
            "was run, so this job makes **no claim** about quality. If this prompt "
            "*should* be gated, add it to the routing map.",
        ]
        return "\n".join(lines) + "\n"

    lines += [f"| targets | {len(jobs)} |", "",
              "| target | verdict |", "|---|---|"]
    for i, job in enumerate(jobs):
        if results is None:
            verdict = "queued"
        else:
            code = results[i]
            verdict = "✅ PASSED" if code == 0 else f"❌ FAILED (exit {code})"
        lines.append(f"| {job.label} | {verdict} |")

    if results is not None and any(results):
        lines += ["", "**Gate failed — this prompt version must not stay on "
                      "`production`.** Roll the label back to the previous "
                      "version, or fix the regression and re-promote."]
    elif results is not None:
        lines += ["", "All mapped gates passed for the live `production` prompt."]
    return "\n".join(lines) + "\n"


def write_summary(report: str) -> None:
    """Append the verdict to the Actions job summary, if we are in Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a") as fh:
            fh.write(report)
    except OSError as e:  # a broken summary must never fail the gate
        print(f"[recert] warning: could not write job summary: {e}", file=sys.stderr)


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Re-certify targets affected by a prompt change")
    ap.add_argument("--prompt-name", required=True,
                    help="Changed prompt name (from the Langfuse dispatch payload)")
    ap.add_argument("--model", default=None,
                    help="Optional model override for the re-cert run(s)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    jobs = resolve_recert_plan(args.prompt_name, model=args.model)

    if not jobs:
        print(f"[recert] prompt '{args.prompt_name}' maps to no certification "
              f"target — nothing to re-certify. This makes no claim about quality.")
        write_summary(render_summary(args.prompt_name, args.model, jobs))
        return 0

    results: list[int] = []
    for job in jobs:
        print(f"\n[recert] {job.label}\n[recert] $ {' '.join(job.argv)}", flush=True)
        rc = subprocess.call(job.argv)
        results.append(rc)
        if rc != 0:
            print(f"[recert] FAILED gate: {job.label} (exit {rc})", file=sys.stderr)

    write_summary(render_summary(args.prompt_name, args.model, jobs, results))

    failures = sum(1 for rc in results if rc != 0)
    if failures:
        print(f"\n[recert] {failures} re-certification(s) failed the gate.", file=sys.stderr)
        return 1
    print("\n[recert] all re-certifications passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
