#!/usr/bin/env python3
"""
Run Use-Case (Agent) Certification Experiments

Where run_certification.py certifies a *model* (one LLM call per item, one score,
a flat trace), this runner certifies a *use case*: a multi-step agent (plan ->
retrieve -> compute -> compose) emitting a nested trace, scored on several
production-readiness dimensions at once, and gated PASS/FAIL only if EVERY
dimension clears its threshold.

The agent for each use case lives in agents/ and self-registers in AGENT_REGISTRY.
This runner reuses the existing dataset loaders, experiment harness, score configs,
annotation queues, and portal — it sets metadata.model = "usecase:<name>" so the
portal renders each use case as a dashboard row with no portal change.

Usage:
    python run_usecase_certification.py --list
    python run_usecase_certification.py --use-case 10k-analyst \
        --dataset certification/financebench-sample --model claude-sonnet-4-6
    python run_usecase_certification.py --use-case sentiment-triage \
        --dataset certification/fpb-sample --model claude-sonnet-4-6 --ci

Environment variables:
    LANGFUSE_PUBLIC_KEY   (required)
    LANGFUSE_SECRET_KEY   (required)
    LANGFUSE_BASE_URL     (default: https://cloud.langfuse.com)
    ANTHROPIC_API_KEY     (required - agents call Claude via the native SDK)
    JUDGE_MODEL           (default: claude-sonnet-4-6 - used by groundedness judge)
"""

import argparse
import os
import sys
from datetime import datetime

# Same OTel backpressure defaults as run_certification.py: agent traces are
# larger (multiple spans + CoT), so keep the export queue draining ahead of fill
# against a slow local Langfuse. See README "Hangs on long runs".
os.environ.setdefault("OTEL_BSP_MAX_QUEUE_SIZE", "20000")
os.environ.setdefault("OTEL_BSP_SCHEDULE_DELAY", "2000")
os.environ.setdefault("OTEL_BSP_MAX_EXPORT_BATCH_SIZE", "64")
os.environ.setdefault("OTEL_BSP_EXPORT_TIMEOUT", "120000")
os.environ.setdefault("LANGFUSE_FLUSH_AT", "64")
os.environ.setdefault("LANGFUSE_FLUSH_INTERVAL", "2")

try:
    from langfuse import get_client
except ImportError:
    print("Error: langfuse package not installed. Run: pip install 'langfuse>=3.0,<4.0'",
          file=sys.stderr)
    sys.exit(1)

from agents import AGENT_REGISTRY
from evaluators import average_score_evaluator, usecase_certification_gate
from cert_common import persist_run_evaluations, queue_failed_items

# Known use cases, even before their agent modules land (issues #9/#10/#11). Used
# for CLI choices + a friendly "not implemented yet" message.
KNOWN_USE_CASES = ["10k-analyst", "sentiment-triage", "advisory-draft"]


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Run LLM use-case (agent) certification experiments via Langfuse"
    )
    parser.add_argument("--use-case", type=str, choices=KNOWN_USE_CASES,
                        help="Which agent use case to certify")
    parser.add_argument("--dataset", type=str,
                        help="Langfuse dataset name (e.g. certification/financebench-sample)")
    parser.add_argument("--model", type=str,
                        default=os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
                        help="Model the agent runs on (default: claude-sonnet-4-6)")
    parser.add_argument("--max-concurrency", type=int, default=5,
                        help="Max concurrent items (default: 5)")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Custom run name (default: auto-generated)")
    parser.add_argument("--queue-failures", action="store_true",
                        help="Route failed items to the 'Certification Review' "
                             "annotation queue for human review")
    parser.add_argument("--ci", action="store_true",
                        help="CI mode: exit with code 1 if certification fails")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview dataset items without running the agent")
    parser.add_argument("--list", action="store_true",
                        help="List registered use cases and exit")
    return parser.parse_args()


def list_use_cases():
    print("Use cases:", file=sys.stderr)
    for name in KNOWN_USE_CASES:
        entry = AGENT_REGISTRY.get(name)
        if entry:
            dims = ", ".join(f"{k}>={v:.0%}" for k, v in entry["gate_thresholds"].items())
            print(f"  [registered] {name:18s} {entry['description']}", file=sys.stderr)
            print(f"               dataset hint: {entry['dataset_hint']}", file=sys.stderr)
            print(f"               gate: {dims}", file=sys.stderr)
        else:
            print(f"  [pending]    {name:18s} agent not implemented yet "
                  f"(see GitHub issues #9/#10/#11)", file=sys.stderr)


# --------------- Annotation queue routing ---------------

def _should_queue(gate_dims):
    """Queue an item for human review if any gate dimension scored a hard 0,
    or groundedness is weak (<0.5)."""
    def predicate(evaluations):
        for ev in evaluations:
            if ev.name in gate_dims and ev.value == 0.0:
                return True
            if ev.name == "groundedness" and ev.value is not None and ev.value < 0.5:
                return True
        return False
    return predicate


# --------------- Main ---------------

def main():
    args = parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    if args.list:
        list_use_cases()
        return

    if not args.use_case:
        print("Error: --use-case is required (or use --list). "
              f"Choices: {KNOWN_USE_CASES}", file=sys.stderr)
        sys.exit(2)

    entry = AGENT_REGISTRY.get(args.use_case)
    if entry is None:
        print(f"Error: use case '{args.use_case}' is not implemented yet.\n"
              f"  The shared foundation (issue #8) is in place; the agent itself "
              f"lands in a later issue:\n"
              f"    10k-analyst      -> #9\n"
              f"    sentiment-triage -> #10\n"
              f"    advisory-draft   -> #11\n"
              f"  Run with --list to see what is registered.", file=sys.stderr)
        sys.exit(1)

    if not args.dataset:
        print("Error: --dataset is required", file=sys.stderr)
        sys.exit(2)

    # Agents call Claude via the native Anthropic SDK (see agents/base.py).
    if args.model.startswith("claude") and not os.getenv("ANTHROPIC_API_KEY"):
        print(f"Error: ANTHROPIC_API_KEY required for model {args.model}", file=sys.stderr)
        sys.exit(1)

    langfuse = get_client()
    gate_thresholds = entry["gate_thresholds"]
    gate_dims = list(gate_thresholds.keys())

    print("Use-Case Certification Runner", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Use case:    {args.use_case}  ({entry['description']})", file=sys.stderr)
    print(f"  Model:       {args.model}", file=sys.stderr)
    print(f"  Dataset:     {args.dataset}", file=sys.stderr)
    print(f"  Gate dims:   {', '.join(f'{k}>={v:.0%}' for k, v in gate_thresholds.items())}",
          file=sys.stderr)
    print(f"  Concurrency: {args.max_concurrency}", file=sys.stderr)

    try:
        dataset = langfuse.get_dataset(args.dataset)
        print(f"  Items:       {len(dataset.items)}", file=sys.stderr)
    except Exception as e:
        print(f"\nError loading dataset '{args.dataset}': {e}", file=sys.stderr)
        print("Run setup_datasets.py first to create the dataset.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("\n  ** DRY RUN - the agent will not run **\n", file=sys.stderr)
        for item in dataset.items:
            inp = item.input if isinstance(item.input, dict) else {"raw": str(item.input)}
            preview = (inp.get("question") or inp.get("text") or str(inp))[:80]
            print(f"  [{item.id[:8]}] {preview}...", file=sys.stderr)
        return

    # Build the agent task and the evaluator set from the registry (single source).
    task = entry["fn"](model=args.model)
    item_evaluators = entry["item_evaluators"]
    run_evaluators = [average_score_evaluator(dim) for dim in gate_dims]
    run_evaluators.append(usecase_certification_gate(gate_thresholds))

    effective_model = f"usecase:{args.use_case}"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_name = args.run_name or (
        f"{args.use_case}-{args.model}-{args.dataset.split('/')[-1]}-{timestamp}"
    )

    print(f"\n  Run name: {run_name}", file=sys.stderr)
    print(f"  Dashboard row: {effective_model}", file=sys.stderr)
    print(f"  Running agent...\n", file=sys.stderr)

    result = dataset.run_experiment(
        name=args.dataset.split("/")[-1],
        run_name=run_name,
        description=f"Use-case certification: {args.use_case} on {args.model} "
                    f"against {args.dataset}",
        task=task,
        evaluators=item_evaluators,
        run_evaluators=run_evaluators,
        max_concurrency=args.max_concurrency,
        metadata={
            "model": effective_model,
            "use_case": args.use_case,
            "base_model": args.model,
            "dataset": args.dataset,
            "gate_thresholds": gate_thresholds,
        },
    )

    print("=" * 50, file=sys.stderr)
    print("Results:", file=sys.stderr)
    print(result.format(), file=sys.stderr)

    # Persist run-level scores (incl. certification_result) to Langfuse.
    persist_run_evaluations(result)

    # Certification summary
    print("\n" + "=" * 50, file=sys.stderr)
    print("USE-CASE CERTIFICATION SUMMARY", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Use case:  {args.use_case}", file=sys.stderr)
    print(f"  Model:     {args.model}", file=sys.stderr)
    print(f"  Dataset:   {args.dataset}", file=sys.stderr)

    cert = None
    for ev in result.run_evaluations:
        if ev.name == "certification_result":
            cert = ev
            status = "PASSED" if ev.value == 1.0 else "FAILED"
            print(f"  Result:    {status}", file=sys.stderr)
            print(f"  Detail:    {ev.comment}", file=sys.stderr)
            break
    else:
        print("  Result:    NO CERTIFICATION GATE CONFIGURED", file=sys.stderr)

    for ev in result.run_evaluations:
        if ev.name.startswith("avg_"):
            print(f"  {ev.name}: {ev.comment}", file=sys.stderr)

    print(f"\nView details in Langfuse UI > Datasets > {args.dataset} > Runs",
          file=sys.stderr)

    if args.queue_failures and result.item_results:
        queue_failed_items(result.item_results, _should_queue(gate_dims))

    langfuse.flush()

    if args.ci and (cert is None or cert.value != 1.0):
        sys.exit(1)


if __name__ == "__main__":
    main()
