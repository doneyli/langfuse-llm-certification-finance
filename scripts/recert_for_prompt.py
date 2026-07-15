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
"""
from __future__ import annotations

import argparse
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
              f"target — nothing to re-certify.")
        return 0

    failures = 0
    for job in jobs:
        print(f"\n[recert] {job.label}\n[recert] $ {' '.join(job.argv)}", flush=True)
        rc = subprocess.call(job.argv)
        if rc != 0:
            failures += 1
            print(f"[recert] FAILED gate: {job.label} (exit {rc})", file=sys.stderr)

    if failures:
        print(f"\n[recert] {failures} re-certification(s) failed the gate.", file=sys.stderr)
        return 1
    print("\n[recert] all re-certifications passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
