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
    LANGFUSE_BASE_URL    (default: https://cloud.langfuse.com)
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

from evaluators import (
    regulatory_compliance_evaluator,
    response_completeness_evaluator,
)


# --------------- API Helpers ---------------

def _get_auth():
    host = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return host, auth


def _api_get(host, auth, path):
    req = urllib.request.Request(
        f"{host}{path}",
        headers={"Authorization": f"Basic {auth}"},
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def _api_post(host, auth, path, body):
    req = urllib.request.Request(
        f"{host}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


# --------------- Trace Fetching ---------------

def fetch_traces(host, auth, *, hours=1, tags=None, trace_name=None, limit=100):
    """Fetch recent traces from Langfuse."""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

    params = f"limit={limit}"
    if tags:
        for tag in tags:
            params += f"&tags={tag}"
    if trace_name:
        params += f"&name={trace_name}"

    data = _api_get(host, auth, f"/api/public/traces?{params}")
    traces = data.get("data", [])

    # Filter by timestamp (API may not support fromTimestamp directly)
    filtered = []
    for t in traces:
        ts = t.get("timestamp", "")
        if ts >= since:
            filtered.append(t)

    return filtered


def get_scored_trace_ids(host, auth, score_name, trace_ids):
    """Check which traces already have a specific score."""
    if not trace_ids:
        return set()

    scored = set()
    # Fetch scores in batches
    for tid in trace_ids:
        data = _api_get(host, auth,
                        f"/api/public/scores?traceId={tid}&name={score_name}&limit=1")
        if data.get("data"):
            scored.add(tid)

    return scored


# --------------- Evaluation ---------------

def evaluate_trace(trace):
    """Run deterministic evaluators on a trace and return results."""
    output = trace.get("output")
    if not output:
        return []

    # Convert output to string if it's a dict
    if isinstance(output, dict):
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
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview evaluation without posting scores")
    return parser.parse_args()


# --------------- Main ---------------

def main():
    args = parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    host, auth = _get_auth()

    print("Production Monitor", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target:     {host}", file=sys.stderr)
    print(f"  Lookback:   {args.hours}h", file=sys.stderr)
    print(f"  Tags:       {args.tags or 'any'}", file=sys.stderr)
    print(f"  Trace name: {args.trace_name or 'any'}", file=sys.stderr)

    # Fetch recent traces
    traces = fetch_traces(host, auth, hours=args.hours, tags=args.tags,
                          trace_name=args.trace_name, limit=args.limit)
    print(f"  Traces:     {len(traces)}", file=sys.stderr)

    if not traces:
        print("\n  No traces found in the lookback window.", file=sys.stderr)
        return

    # Find traces that haven't been scored for compliance yet
    trace_ids = [t["id"] for t in traces]
    already_scored = get_scored_trace_ids(host, auth, "regulatory_compliance", trace_ids)
    unscored = [t for t in traces if t["id"] not in already_scored]
    print(f"  Unscored:   {len(unscored)}", file=sys.stderr)

    if not unscored:
        print("\n  All traces already scored. Nothing to do.", file=sys.stderr)
        return

    if args.dry_run:
        print("\n  ** DRY RUN - no scores will be posted **\n", file=sys.stderr)

    # Evaluate and score
    scored = 0
    violations = 0
    for trace in unscored:
        evaluations = evaluate_trace(trace)
        if not evaluations:
            continue

        for ev in evaluations:
            if ev.value is None:
                continue

            if not args.dry_run:
                try:
                    _api_post(host, auth, "/api/public/scores", {
                        "traceId": trace["id"],
                        "name": ev.name,
                        "value": ev.value,
                        "comment": ev.comment or "",
                        "dataType": "NUMERIC",
                    })
                except Exception as e:
                    print(f"  Warning: failed to score {trace['id'][:12]}...: {e}",
                          file=sys.stderr)
                    continue

            # Track violations
            if ev.name == "regulatory_compliance" and ev.value == 0.0:
                violations += 1
                trace_url = trace.get("htmlPath", f"{host}/trace/{trace['id']}")
                print(f"  VIOLATION: {ev.comment}", file=sys.stderr)
                print(f"    Trace: {trace_url}", file=sys.stderr)

        scored += 1

    # Summary
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Scored:     {scored} traces", file=sys.stderr)
    print(f"  Violations: {violations}", file=sys.stderr)

    if violations > 0:
        print(f"\n  WARNING: {violations} compliance violation(s) detected!",
              file=sys.stderr)
        sys.exit(1)

    print("  All clean.", file=sys.stderr)


if __name__ == "__main__":
    main()
