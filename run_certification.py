#!/usr/bin/env python3
"""
Run LLM Model Certification Experiments

Runs a Langfuse dataset through a model under test, evaluates outputs with
financial evaluators, and reports pass/fail certification status.

Usage:
    python run_certification.py --dataset certification/financebench-sample
    python run_certification.py --dataset certification/financebench-v1 --model gpt-4o
    python run_certification.py --dataset certification/fpb-sample --evaluators sentiment
    python run_certification.py --dataset certification/financebench-v1 --threshold 0.90
    python run_certification.py --dry-run --dataset certification/financebench-sample

Environment variables:
    LANGFUSE_PUBLIC_KEY   (required)
    LANGFUSE_SECRET_KEY   (required)
    LANGFUSE_BASE_URL     (default: https://cloud.langfuse.com)
    LLM_API_KEY           (required - OpenAI-compatible API key)
    LLM_BASE_URL          (default: https://api.openai.com/v1)
    LLM_MODEL             (default: claude-sonnet-4-6)
    ANTHROPIC_API_KEY     (required for Claude models via native Anthropic SDK)

Prerequisites:
    pip install 'langfuse>=3.0,<4.0' openai
    pip install anthropic   # optional, for native Claude API
"""

import argparse
import base64
import json
import os
import sys
import urllib.request
from datetime import datetime

try:
    from langfuse import get_client, Evaluation
    from langfuse.openai import OpenAI as LangfuseOpenAI
except ImportError:
    print("Error: langfuse package not installed. Run: pip install 'langfuse>=3.0,<4.0'",
          file=sys.stderr)
    sys.exit(1)

try:
    import anthropic
except ImportError:
    anthropic = None

from evaluators import (
    exact_match_evaluator,
    numerical_accuracy_evaluator,
    sentiment_evaluator,
    regulatory_compliance_evaluator,
    response_completeness_evaluator,
    groundedness_evaluator,
    average_score_evaluator,
    certification_gate,
)


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Run LLM model certification experiments via Langfuse"
    )
    parser.add_argument("--dataset", type=str, required=True,
                        help="Langfuse dataset name (e.g., certification/financebench-sample)")
    parser.add_argument("--model", type=str,
                        default=os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
                        help="Model to certify (default: LLM_MODEL env or claude-sonnet-4-6)")
    parser.add_argument("--endpoint", type=str,
                        default=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
                        help="LLM API base URL (default: LLM_BASE_URL env)")
    parser.add_argument("--max-concurrency", type=int, default=5,
                        help="Max concurrent LLM calls (default: 5)")
    parser.add_argument("--threshold", type=float, default=0.85,
                        help="Certification pass threshold (default: 0.85)")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Custom run name (default: auto-generated)")
    parser.add_argument("--evaluators", type=str, default="all",
                        choices=["all", "accuracy", "compliance", "sentiment"],
                        help="Which evaluators to use (default: all applicable)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview dataset items without running experiments")
    parser.add_argument("--queue-failures", action="store_true",
                        help="Route failed items to the 'Certification Review' "
                             "annotation queue for human review")
    return parser.parse_args()


# --------------- LLM Clients ---------------

def is_openai_model(model: str) -> bool:
    """Check if model should use OpenAI-compatible API."""
    return model.startswith("gpt-") or model.startswith("o1") or model.startswith("o3")


def is_claude_native(model: str) -> bool:
    """Check if model should use native Anthropic SDK."""
    return model.startswith("claude") and anthropic is not None


def call_anthropic_native(question: str, model: str) -> str:
    """Call Claude via native Anthropic SDK."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": question}],
    )
    return response.content[0].text


def call_openai_compatible(question: str, model: str, endpoint: str, api_key: str) -> str:
    """Call any model via OpenAI-compatible API with Langfuse auto-tracing."""
    client = LangfuseOpenAI(base_url=endpoint, api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": question}],
    )
    return response.choices[0].message.content


# --------------- Task Function ---------------

def _build_prompt(inp: dict) -> str:
    """Build the full prompt from a dataset item's input.

    If the input includes evidence (excerpts from financial filings),
    include it as context so the model can answer from the source document
    rather than guessing. This simulates a RAG pipeline.
    """
    question = inp.get("question", inp.get("text", ""))
    evidence = inp.get("evidence", [])

    if evidence and any(evidence):
        context_parts = []
        for i, ev in enumerate(evidence, 1):
            if ev:
                context_parts.append(f"--- Source Document Excerpt {i} ---\n{ev}")
        context_block = "\n\n".join(context_parts)

        return (
            f"You are a financial analyst. Answer the question using ONLY the "
            f"provided source document excerpts. Be precise with numbers.\n\n"
            f"{context_block}\n\n"
            f"--- Question ---\n{question}"
        )

    return question


def create_certification_task(model: str, endpoint: str, api_key: str):
    """Create a task function that calls the model under test.

    The task function is called once per dataset item. It sends the input
    to the model and returns the raw output text. If the dataset item
    includes evidence from financial filings, it's included as context.
    """
    def task(*, item, **kwargs):
        # Handle both DatasetItem (.input) and dict (["input"]) formats
        inp = item.input if hasattr(item, 'input') else item.get("input", {})

        if isinstance(inp, str):
            inp = {"text": inp}

        prompt = _build_prompt(inp)

        if not prompt:
            return "Error: no question found in dataset item"

        # Route to appropriate LLM client
        if is_claude_native(model):
            return call_anthropic_native(prompt, model)
        else:
            return call_openai_compatible(prompt, model, endpoint, api_key)

    return task


# --------------- Evaluator Selection ---------------

def select_evaluators(evaluator_mode: str, dataset_name: str, threshold: float):
    """Select item and run evaluators based on dataset type and mode."""
    is_sentiment = "fpb" in dataset_name.lower()

    item_evaluators = []
    run_evaluators = []
    primary_score = None

    if evaluator_mode in ("all", "accuracy"):
        if is_sentiment:
            item_evaluators.append(sentiment_evaluator)
            primary_score = "sentiment_accuracy"
        else:
            item_evaluators.append(numerical_accuracy_evaluator)
            item_evaluators.append(exact_match_evaluator)
            primary_score = "numerical_accuracy"

    if evaluator_mode in ("all", "sentiment") and is_sentiment:
        if sentiment_evaluator not in item_evaluators:
            item_evaluators.append(sentiment_evaluator)
            primary_score = primary_score or "sentiment_accuracy"

    if evaluator_mode in ("all", "compliance"):
        item_evaluators.append(regulatory_compliance_evaluator)

    if evaluator_mode == "all":
        item_evaluators.append(response_completeness_evaluator)

    # LLM-as-a-Judge evaluators (for datasets with source evidence)
    if evaluator_mode == "all" and not is_sentiment:
        item_evaluators.append(groundedness_evaluator)
        run_evaluators.append(average_score_evaluator("groundedness"))

    # Run-level evaluators
    if primary_score:
        run_evaluators.append(average_score_evaluator(primary_score))
        run_evaluators.append(certification_gate(primary_score, threshold))

    return item_evaluators, run_evaluators, primary_score


# --------------- Annotation Queue Routing ---------------

REVIEW_QUEUE_NAME = "Certification Review"


def _queue_failed_items(item_results, primary_score):
    """Route low-scoring traces to the annotation queue for human review.

    An item is queued if its primary accuracy score is 0 or its groundedness
    score is below 0.5. Requires the 'Certification Review' annotation queue
    to exist (created by setup_annotation_queues.py).
    """
    lf_host = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
    lf_pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    lf_sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    lf_auth = base64.b64encode(f"{lf_pk}:{lf_sk}".encode()).decode()
    headers = {
        "Authorization": f"Basic {lf_auth}",
        "Content-Type": "application/json",
    }

    # Find the queue ID
    try:
        req = urllib.request.Request(
            f"{lf_host}/api/public/annotation-queues?limit=100",
            headers=headers,
        )
        resp = urllib.request.urlopen(req)
        queues = json.loads(resp.read()).get("data", [])
        queue_id = None
        for q in queues:
            if q["name"] == REVIEW_QUEUE_NAME:
                queue_id = q["id"]
                break
        if not queue_id:
            print(f"  Warning: annotation queue '{REVIEW_QUEUE_NAME}' not found. "
                  f"Run setup_annotation_queues.py first.", file=sys.stderr)
            return
    except Exception as e:
        print(f"  Warning: could not list annotation queues: {e}", file=sys.stderr)
        return

    # Identify failed items
    queued = 0
    for ir in item_results:
        if not hasattr(ir, "trace_id") or not ir.trace_id:
            continue

        should_queue = False
        for ev in ir.evaluations:
            if primary_score and ev.name == primary_score and ev.value == 0.0:
                should_queue = True
                break
            if ev.name == "groundedness" and ev.value is not None and ev.value < 0.5:
                should_queue = True
                break

        if not should_queue:
            continue

        try:
            body = json.dumps({
                "objectId": ir.trace_id,
                "objectType": "TRACE",
                "status": "PENDING",
            }).encode()
            req = urllib.request.Request(
                f"{lf_host}/api/public/annotation-queues/{queue_id}/items",
                data=body,
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req)
            queued += 1
        except Exception as e:
            print(f"  Warning: failed to queue trace {ir.trace_id[:12]}...: {e}",
                  file=sys.stderr)

    if queued:
        print(f"\n  Queued {queued} items for human review in '{REVIEW_QUEUE_NAME}'",
              file=sys.stderr)


# --------------- Main ---------------

def main():
    args = parse_args()

    # Load .env if available (override=True so .env takes precedence over shell env)
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    # Validate credentials
    api_key = os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))
    if is_claude_native(args.model):
        if not os.getenv("ANTHROPIC_API_KEY"):
            print(f"Error: ANTHROPIC_API_KEY required for model {args.model}", file=sys.stderr)
            sys.exit(1)
    elif not api_key:
        print("Error: LLM_API_KEY (or OPENAI_API_KEY) required", file=sys.stderr)
        sys.exit(1)

    # Initialize Langfuse
    langfuse = get_client()

    print("LLM Certification Runner", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Model:       {args.model}", file=sys.stderr)
    print(f"  Endpoint:    {args.endpoint}", file=sys.stderr)
    print(f"  Dataset:     {args.dataset}", file=sys.stderr)
    print(f"  Threshold:   {args.threshold:.0%}", file=sys.stderr)
    print(f"  Concurrency: {args.max_concurrency}", file=sys.stderr)

    # Load dataset
    try:
        dataset = langfuse.get_dataset(args.dataset)
        print(f"  Items:       {len(dataset.items)}", file=sys.stderr)
    except Exception as e:
        print(f"\nError loading dataset '{args.dataset}': {e}", file=sys.stderr)
        print("Run setup_datasets.py first to create the dataset.", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("\n  ** DRY RUN - no experiments will be run **\n", file=sys.stderr)
        for item in dataset.items:
            inp = item.input if isinstance(item.input, dict) else {"raw": str(item.input)}
            preview = inp.get("question", inp.get("text", str(inp)))[:80]
            print(f"  [{item.id[:8]}] {preview}...", file=sys.stderr)
        return

    # Select evaluators
    item_evaluators, run_evaluators, primary_score = select_evaluators(
        args.evaluators, args.dataset, args.threshold
    )
    print(f"  Evaluators:  {[e.__name__ if hasattr(e, '__name__') else str(e) for e in item_evaluators]}", file=sys.stderr)

    # Generate run name
    run_name = args.run_name or (
        f"{args.model}-{args.dataset.split('/')[-1]}-"
        f"{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )

    # Run experiment
    print(f"\n  Run name: {run_name}", file=sys.stderr)
    print(f"  Running experiment...\n", file=sys.stderr)

    result = dataset.run_experiment(
        name=args.dataset.split("/")[-1],
        run_name=run_name,
        description=f"Model certification: {args.model} against {args.dataset}",
        task=create_certification_task(args.model, args.endpoint, api_key),
        evaluators=item_evaluators,
        run_evaluators=run_evaluators,
        max_concurrency=args.max_concurrency,
        metadata={
            "model": args.model,
            "endpoint": args.endpoint,
            "dataset": args.dataset,
            "threshold": args.threshold,
            "evaluator_mode": args.evaluators,
        },
    )

    # Print results
    print("=" * 50, file=sys.stderr)
    print("Results:", file=sys.stderr)
    print(result.format(), file=sys.stderr)

    # Persist run-level evaluations to Langfuse.
    # The Langfuse SDK computes run_evaluators locally but does not store them.
    # We post scores via the REST API, attaching them to the first experiment
    # trace so they appear in the Langfuse UI under that trace's scores.
    if result.run_evaluations and result.item_results:
        first_trace_id = None
        for ir in result.item_results:
            if hasattr(ir, "trace_id") and ir.trace_id:
                first_trace_id = ir.trace_id
                break

        if first_trace_id:
            lf_host = os.getenv("LANGFUSE_BASE_URL", "https://cloud.langfuse.com")
            lf_pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
            lf_sk = os.getenv("LANGFUSE_SECRET_KEY", "")
            lf_auth = base64.b64encode(f"{lf_pk}:{lf_sk}".encode()).decode()

            for ev in result.run_evaluations:
                if ev.value is not None:
                    try:
                        body = json.dumps({
                            "traceId": first_trace_id,
                            "name": ev.name,
                            "value": ev.value,
                            "comment": ev.comment or "",
                            "dataType": "NUMERIC",
                        }).encode()
                        req = urllib.request.Request(
                            f"{lf_host}/api/public/scores",
                            data=body,
                            headers={
                                "Authorization": f"Basic {lf_auth}",
                                "Content-Type": "application/json",
                            },
                            method="POST",
                        )
                        urllib.request.urlopen(req)
                    except Exception as e:
                        print(f"  Warning: failed to persist {ev.name}: {e}",
                              file=sys.stderr)

    # Print certification summary
    print("\n" + "=" * 50, file=sys.stderr)
    print("CERTIFICATION SUMMARY", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Model:     {args.model}", file=sys.stderr)
    print(f"  Dataset:   {args.dataset}", file=sys.stderr)
    print(f"  Threshold: {args.threshold:.0%}", file=sys.stderr)

    for ev in result.run_evaluations:
        if ev.name == "certification_result":
            status = "PASSED" if ev.value == 1.0 else "FAILED"
            print(f"  Result:    {status}", file=sys.stderr)
            print(f"  Detail:    {ev.comment}", file=sys.stderr)
            break
    else:
        print("  Result:    NO CERTIFICATION GATE CONFIGURED", file=sys.stderr)

    for ev in result.run_evaluations:
        if ev.name.startswith("avg_"):
            print(f"  {ev.name}: {ev.comment}", file=sys.stderr)

    print(f"\nView details in Langfuse UI > Datasets > {args.dataset} > Runs", file=sys.stderr)

    # Route failed items to annotation queue for human review
    if args.queue_failures and result.item_results:
        _queue_failed_items(result.item_results, primary_score)

    # Flush
    langfuse.flush()


if __name__ == "__main__":
    main()
