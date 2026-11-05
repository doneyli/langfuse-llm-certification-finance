#!/usr/bin/env python3
"""
Production Monitoring for Financial LLM Traces

Fetches recent production traces from Langfuse and runs deterministic
evaluators on any that haven't been scored yet. Posts scores back to
Langfuse and reports violations.

Designed to run on a schedule (e.g., every 15 minutes via cron) to
continuously monitor production traffic for compliance violations,
quality degradation, and other issues.

Usage:
    python monitor_production.py                          # Score unscored traces from last hour
    python monitor_production.py --hours 24               # Look back 24 hours
    python monitor_production.py --tags production        # Filter by tag
    python monitor_production.py --trace-name my-app      # Filter by trace name
    python monitor_production.py --dry-run                # Preview without scoring

Environment variables:
    LANGFUSE_PUBLIC_KEY  (required)
    LANGFUSE_SECRET_KEY  (required)
    LANGFUSE_BASE_URL    (falls back to LANGFUSE_HOST, then https://cloud.langfuse.com)
"""

import argparse
import json
import os
import sys

from evaluators import (
    regulatory_compliance_evaluator,
    response_completeness_evaluator,
)
from cert_common import (
    PAGE_SIZE,
    iso_utc,
    langfuse_creds,
    paginate_cursor,
    parse_json_field,
    queue_trace_ids,
)


# --------------- Langfuse v4 reads ---------------
#
# Production "traces" are read as their root observations (GET
# /v2/observations?isRootObservation=true): in Langfuse v4 a trace's input and
# output are its root observation's, and the legacy GET /traces endpoint was
# removed on Langfuse Cloud on 2026-11-16.

OBSERVATION_FIELDS = "core,basic,io,trace_context"

# Gate runs (run_experiment) land in this environment and are already scored
# by the gate itself; monitoring them again would double-count.
EXPERIMENT_ENVIRONMENT = "sdk-experiment"

SCORE_LOOKUP_BATCH = 50


def observation_filter(*, tags=None, trace_name=None):
    """The /v2/observations ``filter`` array for --tags / --trace-name."""
    filters = []
    if trace_name:
        filters.append({"type": "string", "column": "traceName",
                        "operator": "=", "value": trace_name})
    if tags:
        filters.append({"type": "arrayOptions", "column": "tags",
                        "operator": "any of", "value": list(tags)})
    return filters


def fetch_traces(*, hours=1, tags=None, trace_name=None, limit=100,
                 include_experiments=False):
    """Recent production traces, as their root observations.

    Each row carries ``traceId``, ``id`` (the root observation id), ``input``,
    ``output`` (raw strings), ``traceName``, ``tags`` and ``environment``.
    """
    params = {
        "isRootObservation": True,
        "fromStartTime": iso_utc(hours_ago=hours),
        "toStartTime": iso_utc(),
        "fields": OBSERVATION_FIELDS,
    }
    filters = observation_filter(tags=tags, trace_name=trace_name)
    if filters:
        params["filter"] = filters
    rows = paginate_cursor("/api/public/v2/observations", params,
                           max_pages=max(1, -(-limit // PAGE_SIZE)),
                           limit=min(limit, PAGE_SIZE))
    if not include_experiments:
        rows = [r for r in rows if r.get("environment") != EXPERIMENT_ENVIRONMENT]
    return rows[:limit]


def _score_trace_id(score):
    subject = score.get("subject") or {}
    return score.get("traceId") or (subject.get("traceId")
                                    if isinstance(subject, dict) else None)


def get_scored_trace_ids(score_name, trace_ids):
    """Trace ids (of ``trace_ids``) that already carry ``score_name``.

    One GET /v3/scores per batch of 50 trace ids (comma-separated filter).
    """
    scored = set()
    ids = list(dict.fromkeys(t for t in trace_ids if t))
    for i in range(0, len(ids), SCORE_LOOKUP_BATCH):
        batch = ids[i:i + SCORE_LOOKUP_BATCH]
        rows = paginate_cursor("/api/public/v3/scores", {
            "name": score_name, "traceId": ",".join(batch), "fields": "subject",
        })
        scored.update(t for t in map(_score_trace_id, rows) if t)
    return scored


def post_score(trace, ev):
    """Write one evaluation onto the trace's root observation via the SDK."""
    from langfuse import get_client
    get_client().create_score(
        trace_id=trace["traceId"],
        observation_id=trace.get("id"),
        name=ev.name,
        value=ev.value,
        data_type=getattr(ev, "data_type", None) or "NUMERIC",
        comment=ev.comment or "",
    )


# --------------- Evaluation ---------------

def evaluate_trace(trace):
    """Run deterministic evaluators on a trace's output and return results.

    v4 returns ``output`` as a raw string. A JSON agent result
    (``{"answer": ...}``) is scored on its answer text; any other JSON is
    scored as its serialized form; plain text as-is.
    """
    output = parse_json_field(trace.get("output"))
    if not output:
        return []

    if isinstance(output, dict) and isinstance(output.get("answer"), str):
        output = output["answer"]
    elif isinstance(output, (dict, list)):
        output = json.dumps(output)

    results = []

    # Regulatory compliance
    ev = regulatory_compliance_evaluator(output=output)
    results.append(ev)

    # Response completeness
    ev = response_completeness_evaluator(output=output)
    results.append(ev)

    return results


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Monitor production traces with financial evaluators"
    )
    parser.add_argument("--hours", type=float, default=1,
                        help="Look back N hours (default: 1)")
    parser.add_argument("--tags", type=str, nargs="*", default=None,
                        help="Filter traces by tags (e.g., --tags production)")
    parser.add_argument("--trace-name", type=str, default=None,
                        help="Filter traces by name")
    parser.add_argument("--limit", type=int, default=100,
                        help="Max traces to process (default: 100)")
    parser.add_argument("--include-experiments", action="store_true",
                        help="Also score deployment-gate experiment traces "
                             f"(environment '{EXPERIMENT_ENVIRONMENT}'; skipped "
                             "by default — the gate already scored them)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview evaluation without posting scores")
    parser.add_argument("--queue-violations", action="store_true",
                        help="Route traces with a compliance violation to the "
                             "'Certification Review' annotation queue for human "
                             "review (the Monitor -> human-review feedback edge)")
    return parser.parse_args()


# --------------- Main ---------------

def main():
    args = parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    if not (os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")):
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required",
              file=sys.stderr)
        sys.exit(1)

    host, _auth = langfuse_creds()

    print("Production Monitor", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target:     {host}", file=sys.stderr)
    print(f"  Lookback:   {args.hours}h", file=sys.stderr)
    print(f"  Tags:       {args.tags or 'any'}", file=sys.stderr)
    print(f"  Trace name: {args.trace_name or 'any'}", file=sys.stderr)

    # Fetch recent traces
    traces = fetch_traces(hours=args.hours, tags=args.tags,
                          trace_name=args.trace_name, limit=args.limit,
                          include_experiments=args.include_experiments)
    print(f"  Traces:     {len(traces)}", file=sys.stderr)

    if not traces:
        print("\n  No traces found in the lookback window.", file=sys.stderr)
        return

    # Find traces that haven't been scored for compliance yet
    trace_ids = [t["traceId"] for t in traces]
    already_scored = get_scored_trace_ids("regulatory_compliance", trace_ids)
    unscored = [t for t in traces if t["traceId"] not in already_scored]
    print(f"  Unscored:   {len(unscored)}", file=sys.stderr)

    if not unscored:
        print("\n  All traces already scored. Nothing to do.", file=sys.stderr)
        return

    if args.dry_run:
        print("\n  ** DRY RUN - no scores will be posted **\n", file=sys.stderr)

    # Evaluate and score
    scored = 0
    violations = 0
    violation_trace_ids = []
    for trace in unscored:
        evaluations = evaluate_trace(trace)
        if not evaluations:
            continue

        for ev in evaluations:
            if ev.value is None:
                continue

            if not args.dry_run:
                try:
                    post_score(trace, ev)
                except Exception as e:
                    print(f"  Warning: failed to score {trace['traceId'][:12]}...: {e}",
                          file=sys.stderr)
                    continue

            # Track violations
            if ev.name == "regulatory_compliance" and ev.value == 0.0:
                violations += 1
                violation_trace_ids.append(trace["traceId"])
                trace_url = f"{host}/trace/{trace['traceId']}"
                print(f"  VIOLATION: {ev.comment}", file=sys.stderr)
                print(f"    Trace: {trace_url}", file=sys.stderr)

        scored += 1

    if scored and not args.dry_run:
        from langfuse import get_client
        get_client().flush()

    # Summary
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Scored:     {scored} traces", file=sys.stderr)
    print(f"  Violations: {violations}", file=sys.stderr)

    # Feedback edge: route flagged traces to the human-review queue so a real
    # failure can later be promoted into the golden dataset
    # (see promote_trace_to_dataset.py). Deliberately opt-in via --queue-violations.
    if args.queue_violations and violation_trace_ids and not args.dry_run:
        queue_trace_ids(violation_trace_ids)
    elif args.queue_violations and violation_trace_ids and args.dry_run:
        print(f"  [dry-run] would queue {len(violation_trace_ids)} trace(s) "
              f"for human review", file=sys.stderr)

    if violations > 0:
        print(f"\n  WARNING: {violations} compliance violation(s) detected!",
              file=sys.stderr)
        sys.exit(1)

    print("  All clean.", file=sys.stderr)


if __name__ == "__main__":
    main()
