#!/usr/bin/env python3
"""
SPIKE (disposable): verify the load-bearing assumptions of the deployment gate
on Langfuse v4 + Python SDK v5.

Runs ONE item of a real (server-side) dataset through ``run_experiment`` with a
task that opens nested observations, a run-level evaluator, and gate-shaped run
metadata, then reads everything back through the v4 read API:

  (a) GET /experiments?name=<run>&fields=core,metadata,scores
        -> does metadata.gate_thresholds round-trip, and in what shape?
  (b)   -> do run-level Evaluations land on the experiment's scores?
  (c) GET /v2/observations?traceId=<item trace>
        -> do the retriever / tool / generation observations nest under the
           item trace the way the agents assume?

Runs are named ``spike-v5-<timestamp>`` so they are obviously disposable.

Usage: uv run python scripts/spike_span_nesting.py [--dataset certification/advisory-adversarial]
"""

import argparse
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(override=True)

from langfuse import Evaluation, get_client
from agents.base import traced_generation
from cert_common import (api_get, iso_utc, normalize_run_metadata,
                         paginate_cursor, recorded_gate)

SPIKE_MODEL = "claude-haiku-4-5-20251001"
SPIKE_METADATA = {"model": "spike", "gate_thresholds": {"x": 0.5}, "threshold": 0.85}


def task(*, item, **kwargs):
    lf = get_client()
    with lf.start_as_current_observation(as_type="retriever", name="retrieve") as r:
        r.update(input={"q": item.input}, output={"operands": {"a": 6489, "b": 267.5}})
        with lf.start_as_current_observation(as_type="tool", name="calculate") as t:
            result = 6489 / 267.5
            t.update(input={"expr": "6489/267.5"}, output={"result": round(result, 2)})
        ans = traced_generation(
            name="classify", model=SPIKE_MODEL, max_tokens=8,
            system="Reply with one word.",
            user="Say the word: turnover",
        )
    return {"answer": ans, "trajectory": {"tools_used": ["calculate"]}}


def spike_run_evaluator(*, item_results, **kwargs):
    return Evaluation(name="spike_run_score", value=1.0, comment="run-level spike")


def retry(fn, tries=20, delay=3):
    for i in range(tries):
        out = fn()
        if out:
            return out
        print(f"  ...not queryable yet (try {i+1}/{tries})", file=sys.stderr)
        time.sleep(delay)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="certification/advisory-adversarial")
    args = ap.parse_args()

    lf = get_client()
    dataset = lf.get_dataset(args.dataset)
    run_name = f"spike-v5-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    started = iso_utc(hours_ago=1)
    print(f"Running 1-item experiment '{run_name}' on {args.dataset}...", file=sys.stderr)
    result = lf.run_experiment(
        name="spike-v5",
        run_name=run_name,
        data=dataset.items[:1],
        task=task,
        run_evaluators=[spike_run_evaluator],
        metadata=SPIKE_METADATA,
    )
    lf.flush()
    print(f"experiment_id={result.experiment_id}\nexperiment_url={result.experiment_url}",
          file=sys.stderr)

    ir = result.item_results[0]
    trace_id = getattr(ir, "trace_id", None)
    ok = True

    # (a) + (b): experiment metadata and run-level scores
    def fetch_experiment():
        rows = api_get("/api/public/experiments", {
            "name": run_name, "fromStartTime": started,
            "fields": "core,metadata,scores"}).get("data") or []
        row = rows[0] if rows else None
        return row if row and row.get("scores") else None

    exp = retry(fetch_experiment)
    if not exp:
        print("FAIL: experiment not found via GET /experiments", file=sys.stderr)
        sys.exit(1)
    raw_meta = exp.get("metadata")
    print(f"\n(a) raw metadata: {raw_meta!r}", file=sys.stderr)
    meta = normalize_run_metadata(raw_meta)
    gate = recorded_gate(meta)
    print(f"    normalized gate_thresholds={meta.get('gate_thresholds')!r} "
          f"-> recorded_gate={gate}", file=sys.stderr)
    if gate != {"x": 0.5}:
        print("FAIL: gate_thresholds does not round-trip", file=sys.stderr); ok = False
    names = sorted(s.get("name") for s in exp.get("scores") or [])
    print(f"(b) run-level scores on experiment: {names}", file=sys.stderr)
    if "spike_run_score" not in names:
        print("FAIL: run-level evaluation missing on experiment", file=sys.stderr); ok = False

    # (c) nesting under the item trace
    obs = retry(lambda: paginate_cursor("/api/public/v2/observations", {
        "traceId": trace_id, "fromStartTime": started,
        "fields": "core,basic,model,usage"}) if trace_id else None)
    obs = obs or []
    by_id = {o["id"]: o for o in obs}

    def depth(o):
        d, p = 0, o.get("parentObservationId")
        while p and p in by_id:
            d += 1
            p = by_id[p].get("parentObservationId")
        return d

    print(f"(c) trace {trace_id} has {len(obs)} observations:", file=sys.stderr)
    for o in sorted(obs, key=depth):
        print(f"      {'  ' * depth(o)}- {o.get('name')} [{o.get('type')}]",
              file=sys.stderr)
    by_name = {o.get("name"): o for o in obs}
    calc, retr, gen = by_name.get("calculate"), by_name.get("retrieve"), by_name.get("classify")
    if not (calc and retr and calc.get("parentObservationId") == retr["id"]):
        print("FAIL: 'calculate' does not nest under 'retrieve'", file=sys.stderr); ok = False
    if not (gen and retr and gen.get("parentObservationId") == retr["id"]):
        print("FAIL: generation 'classify' missing or not under 'retrieve'",
              file=sys.stderr); ok = False
    else:
        print(f"    generation model={gen.get('providedModelName') or gen.get('model')} "
              f"usage={gen.get('usageDetails')}", file=sys.stderr)
    if retr and retr.get("type") != "RETRIEVER":
        print(f"WARN: 'retrieve' type is {retr.get('type')}", file=sys.stderr)

    print("\nPASS" if ok else "\nFAIL", file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
