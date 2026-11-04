#!/usr/bin/env python3
"""Promote a flagged production trace into a golden dataset item.

This closes loop edge A (see docs/ai-engineering-loop.md): the
observation -> development feedback edge. A real production failure surfaced by
monitoring / human review becomes a **repeatable test case**, so the next
certification run regression-tests it and the system never silently regresses on
that scenario again.

    monitor_production.py --queue-violations   # Monitor flags -> review queue
              │  (a human reviews the queued trace in the Langfuse UI)
              ▼
    promote_trace_to_dataset.py --from-queue   # reviewed trace -> golden dataset
              │
              ▼
    run_usecase_certification.py ...            # next Experiment includes it

Design choices (deliberately human-gated):
- We capture the trace **input** (the scenario) — that is the durable value. We do
  NOT copy the trace output as the expected answer: a flagged trace's output is by
  definition suspect, and auto-promoting it would poison the golden set. Supply the
  correct answer with --expected, or leave it blank (metadata flags
  `needs_expected_review`) for a reviewer to fill in the Langfuse UI.
- Items are **idempotent**: the dataset-item id is derived from the trace id
  (`prod-<traceId>`), so re-running upserts instead of duplicating. `source_trace_id`
  links the item back to its origin trace for provenance/audit.

Usage:
    python promote_trace_to_dataset.py --dataset certification/financebench-sample \
        --trace-id <id> [--trace-id <id> ...] [--expected "The FY2019 ... is 24.26"]
    python promote_trace_to_dataset.py --dataset certification/financebench-sample \
        --from-queue                       # promote everything in the review queue
    python promote_trace_to_dataset.py --dataset ... --trace-id <id> --dry-run
"""
from __future__ import annotations

import argparse
import sys


def item_id_for(trace_id: str) -> str:
    """Deterministic dataset-item id for a promoted trace (=> idempotent upsert)."""
    return f"prod-{trace_id}"


def build_dataset_item(trace: dict, *, expected: str | None = None,
                       note: str | None = None) -> dict:
    """Build a dataset-item payload from a fetched trace.

    expected: the correct answer, if the reviewer supplies one. When omitted the
    item is created with an empty expected answer and flagged `needs_expected_review`
    so it is clearly not yet a usable golden case.
    """
    trace_id = trace.get("id")
    metadata = {
        "promoted_from": "production",
        "source_trace_id": trace_id,
        "needs_expected_review": expected is None,
    }
    if note:
        metadata["note"] = note
    return {
        "id": item_id_for(trace_id),
        "input": trace.get("input"),
        "expected_output": {"answer": expected if expected is not None else ""},
        "metadata": metadata,
        "source_trace_id": trace_id,
    }


def resolve_trace_ids(args, *, from_queue_fn) -> list[str]:
    """Resolve the trace ids to promote from CLI args (explicit ids and/or queue)."""
    trace_ids = list(args.trace_id or [])
    if args.from_queue:
        trace_ids += from_queue_fn()
    # de-dupe, preserve order, drop falsy
    return [t for t in dict.fromkeys(trace_ids) if t]


def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Promote flagged production trace(s) into a golden dataset")
    ap.add_argument("--dataset", required=True,
                    help="Target Langfuse dataset (e.g. certification/financebench-sample)")
    ap.add_argument("--trace-id", action="append", default=[],
                    help="Trace id to promote (repeatable)")
    ap.add_argument("--from-queue", action="store_true",
                    help="Also promote every trace currently in the "
                         "'Certification Review' annotation queue")
    ap.add_argument("--expected", default=None,
                    help="Correct answer to store as expected_output (applies to all "
                         "promoted traces; omit to leave for human review)")
    ap.add_argument("--note", default=None, help="Optional note stored in item metadata")
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview without creating dataset items")
    return ap.parse_args(argv)


def trace_from_observations(trace_id: str, rows: list[dict]) -> dict | None:
    """Rebuild the trace view build_dataset_item needs from v4 observation rows.

    In Langfuse v4 a trace's input/output are its root observation's
    (``parentObservationId`` is null). IO comes back as raw strings, so JSON is
    decoded. Returns None when the trace has no observations (yet).
    """
    from cert_common import parse_json_field
    if not rows:
        return None
    roots = [r for r in rows if not r.get("parentObservationId")] or rows
    root = min(roots, key=lambda r: r.get("startTime") or "")
    return {
        "id": trace_id,
        "name": root.get("traceName") or root.get("name"),
        "input": parse_json_field(root.get("input")),
        "output": parse_json_field(root.get("output")),
    }


def _fetch_trace(trace_id, *, lookback_days=None):
    """Fetch a trace's root observation via GET /v2/observations?traceId=.

    The legacy GET /traces/{id} was removed on Langfuse Cloud on 2026-11-16.
    /v2/observations needs a lower time bound; PORTAL_LOOKBACK_DAYS (default
    730) covers any trace a reviewer would promote.
    """
    from cert_common import iso_utc, lookback_from_env, paginate_cursor
    rows = paginate_cursor("/api/public/v2/observations", {
        "traceId": trace_id,
        "fromStartTime": (iso_utc(days_ago=lookback_days) if lookback_days
                          else lookback_from_env()),
        "fields": "core,basic,io,trace_context",
    })
    trace = trace_from_observations(trace_id, rows)
    if trace is None:
        raise LookupError(f"no observations found for trace {trace_id}")
    return trace


def main(argv=None) -> int:
    args = parse_args(argv)

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    from cert_common import fetch_review_queue_trace_ids

    trace_ids = resolve_trace_ids(args, from_queue_fn=fetch_review_queue_trace_ids)
    if not trace_ids:
        print("No trace ids to promote (pass --trace-id and/or --from-queue).",
              file=sys.stderr)
        return 1

    print(f"Promoting {len(trace_ids)} trace(s) -> dataset '{args.dataset}'",
          file=sys.stderr)
    if args.expected is None:
        print("  (no --expected given: items are flagged needs_expected_review; "
              "set the correct answer in the Langfuse UI before relying on them)",
          file=sys.stderr)

    from langfuse import get_client
    client = get_client()

    promoted = 0
    for tid in trace_ids:
        try:
            trace = _fetch_trace(tid)
        except Exception as e:
            print(f"  Warning: could not fetch trace {tid[:12]}...: {e}", file=sys.stderr)
            continue
        item = build_dataset_item(trace, expected=args.expected, note=args.note)
        if args.dry_run:
            print(f"  [dry-run] {item['id']}: input={str(item['input'])[:60]!r}",
                  file=sys.stderr)
            promoted += 1
            continue
        try:
            client.create_dataset_item(
                dataset_name=args.dataset,
                id=item["id"],
                input=item["input"],
                expected_output=item["expected_output"],
                metadata=item["metadata"],
                source_trace_id=item["source_trace_id"],
            )
            promoted += 1
            print(f"  Promoted {item['id']}", file=sys.stderr)
        except Exception as e:
            print(f"  Error promoting trace {tid[:12]}...: {e}", file=sys.stderr)

    verb = "would promote" if args.dry_run else "promoted"
    print(f"\n  {verb} {promoted}/{len(trace_ids)} trace(s) into '{args.dataset}'.",
          file=sys.stderr)
    return 0 if promoted else 1


if __name__ == "__main__":
    raise SystemExit(main())
