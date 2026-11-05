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
    print("Error: langfuse package not installed. Run: pip install 'langfuse>=5,<6'",
          file=sys.stderr)
    sys.exit(1)

from agents import AGENT_REGISTRY
from agents.base import as_agent_task
from evaluators import average_score_evaluator, measure_bars, usecase_certification_gate
from cert_common import dataset_version, queue_failed_items

# All three agents ship registered. The list stays separate from AGENT_REGISTRY
# because the agents package tolerates a *missing* agent module file
# (ModuleNotFoundError for the module itself — see agents/__init__.py), so the
# CLI can still --list and report the gap. Any other import-time error inside an
# agent module propagates loudly by design.
KNOWN_USE_CASES = ["10k-analyst", "sentiment-triage", "advisory-draft"]

# Module implementing each use case, for the debug hints below — slugs do not
# map 1:1 to module names (10k-analyst lives in financial_analyst).
_AGENT_MODULE = {
    "10k-analyst": "agents.financial_analyst",
    "sentiment-triage": "agents.sentiment_triage",
    "advisory-draft": "agents.advisory_draft",
}


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
                        help="CI mode: exit with code 1 if certification fails "
                             "(or, with --baseline, if any case newly fails)")
    parser.add_argument("--baseline", type=str, default=None, metavar="EXPERIMENT",
                        help="Name of a reviewed experiment on the same dataset. "
                             "Items that cleared every gated dimension there but "
                             "fail one now are reported as newly failing cases "
                             "(an average can hide them); with --ci they fail "
                             "the run. thresholds.json stays the primary bar.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview dataset items without running the agent")
    parser.add_argument("--list", action="store_true",
                        help="List registered use cases and exit")
    return parser.parse_args()


def _fmt_bars(bars):
    return ", ".join(f"{k}>={v:.0%}" for k, v in bars.items())


def list_use_cases():
    print("Use cases:", file=sys.stderr)
    for name in KNOWN_USE_CASES:
        entry = AGENT_REGISTRY.get(name)
        if entry:
            print(f"  [registered] {name:18s} {entry['description']}", file=sys.stderr)
            print(f"               dataset hint: {entry['dataset_hint']}", file=sys.stderr)
            print(f"               gate (required): {_fmt_bars(entry['gate_thresholds'])}",
                  file=sys.stderr)
            if entry.get("advisory_thresholds"):
                print(f"               advisory (does not block): "
                      f"{_fmt_bars(entry['advisory_thresholds'])}", file=sys.stderr)
        else:
            print(f"  [unavailable] {name:18s} agent module missing or not "
                  f"registered — check `python -c 'import "
                  f"{_AGENT_MODULE.get(name, 'agents')}'`", file=sys.stderr)


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


# --------------- Approved-baseline regression check ---------------

def item_clears(scores: dict, gate: dict) -> list:
    """Gated dimensions an item fails: missing, or below its bar."""
    failing = []
    for dim, bar in gate.items():
        value = scores.get(dim)
        if value is None or value < bar:
            failing.append(dim)
    return failing


def newly_failing(baseline: dict, current: dict, gate: dict) -> list:
    """Cases that cleared every gated dimension in the baseline but not now.

    ``baseline`` / ``current`` map dataset item id -> {score name: value}.
    Returns ``[(item_id, [failing dims])]``; items absent from either run are
    not compared.
    """
    out = []
    for item_id, base_scores in baseline.items():
        if item_id not in current or item_clears(base_scores, gate):
            continue
        failing = item_clears(current[item_id], gate)
        if failing:
            out.append((item_id, failing))
    return sorted(out)


def _baseline_item_scores(dataset_id, experiment_name):
    """{dataset item id: {score: value}} for a past experiment (v4 read API)."""
    from cert_common import lookback_from_env, paginate_cursor, score_value
    exps = paginate_cursor("/api/public/experiments", {
        "datasetId": dataset_id, "name": experiment_name,
        "fromStartTime": lookback_from_env(), "fields": "core"})
    exps = [e for e in exps if e.get("name") == experiment_name]
    if not exps:
        return None
    exp = max(exps, key=lambda e: e.get("startTime") or "")
    rows = paginate_cursor("/api/public/experiment-items", {
        "experimentId": exp["id"], "fromStartTime": lookback_from_env(),
        "fields": "core,scores"})
    return {r.get("experimentItemId"): {s.get("name"): score_value(s)
                                        for s in r.get("scores") or []}
            for r in rows if r.get("experimentItemId")}


def _current_item_scores(item_results):
    out = {}
    for ir in item_results or []:
        item = getattr(ir, "item", None)
        item_id = getattr(item, "id", None)
        if not item_id:
            continue
        out[item_id] = {ev.name: (float(ev.value) if isinstance(ev.value, (int, float))
                                  else None)
                        for ev in getattr(ir, "evaluations", []) or []}
    return out


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
        print(f"Error: use case '{args.use_case}' is not registered — its agent "
              f"module is missing or did not self-register.\n"
              f"  Run with --list to see what is registered, or check the import "
              f"directly: python -c 'import "
              f"{_AGENT_MODULE.get(args.use_case, 'agents')}'",
              file=sys.stderr)
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
    advisory_thresholds = entry.get("advisory_thresholds") or {}
    gate_dims = list(gate_thresholds.keys())

    print("Use-Case Certification Runner", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Use case:    {args.use_case}  ({entry['description']})", file=sys.stderr)
    print(f"  Model:       {args.model}", file=sys.stderr)
    print(f"  Dataset:     {args.dataset}", file=sys.stderr)
    print(f"  Gate dims:   {_fmt_bars(gate_thresholds)}", file=sys.stderr)
    if advisory_thresholds:
        print(f"  Advisory:    {_fmt_bars(advisory_thresholds)}  (reported, does not block)",
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
    # Each item runs inside one `agent` observation (usecase:<name>), which
    # gives the trace its Agent Graph in Langfuse.
    task = as_agent_task(entry["fn"](model=args.model), args.use_case)
    item_evaluators = entry["item_evaluators"]
    run_evaluators = [average_score_evaluator(dim)
                      for dim in [*gate_dims, *advisory_thresholds]]
    run_evaluators.append(usecase_certification_gate(gate_thresholds,
                                                     advisory=advisory_thresholds))

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
            # Required bars only. The portal and the evidence pack replay a
            # run's verdict as all() over this key (cert_common.recorded_gate),
            # so advisory bars are recorded under their own key, never here.
            "gate_thresholds": gate_thresholds,
            **({"advisory_thresholds": advisory_thresholds}
               if advisory_thresholds else {}),
            "dataset_version": dataset_version(dataset.items),
            "dataset_item_count": len(dataset.items),
        },
    )

    print("=" * 50, file=sys.stderr)
    print("Results:", file=sys.stderr)
    print(result.format(), file=sys.stderr)

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

    # Advisory bars: same measurement as the gate, never part of the verdict
    # or the --ci exit code.
    if advisory_thresholds:
        print("  Advisory (reported, does not block):", file=sys.stderr)
        rows = measure_bars(advisory_thresholds, result.item_results or [])
        for dim, (avg, met, bar, n) in rows.items():
            print(f"    {dim}: {avg:.1%} vs >={bar:.0%} over {n} items -> "
                  f"{'met' if met else 'MISS'}", file=sys.stderr)

    # Run-level scores (certification_result, avg_*) are stored on the
    # experiment by the SDK itself, with datasetRunId = the experiment id, so
    # GET /api/public/v3/scores?experimentId=<id> returns them. Never re-post
    # them with a traceId: that attaches them to one item's trace instead.
    print(f"\nView in Langfuse (Experiments): "
          f"{getattr(result, 'experiment_url', None) or args.dataset}",
          file=sys.stderr)

    regressions = []
    if args.baseline:
        baseline = _baseline_item_scores(dataset.id, args.baseline)
        if baseline is None:
            print(f"\n  Baseline '{args.baseline}' not found on {args.dataset}; "
                  f"skipping the per-case check.", file=sys.stderr)
        else:
            regressions = newly_failing(
                baseline, _current_item_scores(result.item_results), gate_thresholds)
            print(f"\n  Baseline: {args.baseline} — {len(regressions)} newly failing "
                  f"case(s)", file=sys.stderr)
            for item_id, dims in regressions:
                print(f"    {item_id}: now fails {', '.join(dims)}", file=sys.stderr)

    if args.queue_failures and result.item_results:
        queue_failed_items(result.item_results, _should_queue(gate_dims))

    langfuse.flush()

    if args.ci and (cert is None or cert.value != 1.0 or regressions):
        sys.exit(1)


if __name__ == "__main__":
    main()
