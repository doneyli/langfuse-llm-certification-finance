#!/usr/bin/env python3
"""
SPIKE (#9, throwaway): verify the load-bearing assumption for use-case cert —
that observations opened with start_as_current_observation *inside a
run_experiment task callback* nest under the per-item trace, forming the tree
  trace
   └─ retrieve (span)
       └─ calculate (tool)
       └─ classify (generation, with model + token usage)

Runs run_experiment with ONE local item (no server dataset), then fetches the
trace via the public API and prints the observation parentage so we can confirm
nesting before building the real agent. If this fails, the agent must thread
trace_context explicitly.

Usage: uv run python scripts/spike_span_nesting.py
"""

import base64
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(override=True)

from langfuse import get_client
from agents.base import traced_span, traced_tool, traced_generation

SPIKE_MODEL = "claude-haiku-4-5-20251001"


def task(*, item, **kwargs):
    with traced_span("retrieve") as s:
        s.update(input={"q": item["input"]}, output={"operands": {"a": 6489, "b": 267.5}})
        with traced_tool("calculate") as t:
            result = 6489 / 267.5
            t.update(input={"expr": "6489/267.5"}, output={"result": round(result, 2)})
        # one tiny generation to confirm generation spans attach with usage
        ans = traced_generation(
            name="classify", model=SPIKE_MODEL, max_tokens=8,
            system="Reply with one word.",
            user="Say the word: turnover",
        )
    return {"answer": ans, "trajectory": {"tools_used": ["calculate"]}}


def fetch_trace(host, auth, trace_id, tries=12, delay=3):
    headers = {"Authorization": f"Basic {auth}"}
    for i in range(tries):
        try:
            req = urllib.request.Request(f"{host}/api/public/traces/{trace_id}", headers=headers)
            return json.loads(urllib.request.urlopen(req).read())
        except Exception as e:
            print(f"  ...trace not queryable yet (try {i+1}/{tries}): {e}", file=sys.stderr)
            time.sleep(delay)
    return None


def main():
    lf = get_client()
    print("Running 1-item experiment with nested spans...", file=sys.stderr)
    result = lf.run_experiment(
        name="spike-span-nesting",
        run_name="spike-span-nesting",
        data=[{"input": "FY2019 fixed asset turnover?"}],
        task=task,
    )
    lf.flush()

    ir = result.item_results[0]
    trace_id = getattr(ir, "trace_id", None)
    print(f"\nitem output: {ir.output}", file=sys.stderr)
    print(f"trace_id: {trace_id}", file=sys.stderr)
    if not trace_id:
        print("FAIL: no trace_id on item result", file=sys.stderr)
        sys.exit(1)

    host = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    pk, sk = os.getenv("LANGFUSE_PUBLIC_KEY", ""), os.getenv("LANGFUSE_SECRET_KEY", "")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()

    trace = fetch_trace(host, auth, trace_id)
    if not trace:
        print("FAIL: could not fetch trace (ingestion lag?)", file=sys.stderr)
        sys.exit(1)

    obs = trace.get("observations", [])
    by_id = {o["id"]: o for o in obs}
    print(f"\nTrace '{trace.get('name')}' has {len(obs)} observations:", file=sys.stderr)

    def depth(o):
        d, p = 0, o.get("parentObservationId")
        while p and p in by_id:
            d += 1
            p = by_id[p].get("parentObservationId")
        return d

    for o in sorted(obs, key=depth):
        print(f"  {'  ' * depth(o)}- {o.get('name')} "
              f"[{o.get('type')}] parent={o.get('parentObservationId')}", file=sys.stderr)

    # Assertions
    names = {o["name"]: o for o in obs}
    ok = True
    if "calculate" in names and "retrieve" in names:
        if names["calculate"].get("parentObservationId") == names["retrieve"]["id"]:
            print("\nPASS: 'calculate' nests under 'retrieve'", file=sys.stderr)
        else:
            print("\nFAIL: 'calculate' is NOT a child of 'retrieve'", file=sys.stderr); ok = False
    else:
        print("\nFAIL: expected observations missing (retrieve/calculate)", file=sys.stderr); ok = False

    gen = names.get("classify")
    if gen:
        u = gen.get("usageDetails") or gen.get("usage") or {}
        print(f"generation 'classify' model={gen.get('model')} usage={u}", file=sys.stderr)
        if not gen.get("model"):
            print("WARN: generation has no model recorded", file=sys.stderr)
    else:
        print("FAIL: generation 'classify' missing", file=sys.stderr); ok = False

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
