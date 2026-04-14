#!/usr/bin/env python3
"""
Export Certification Results for Compliance Reports

Queries Langfuse experiment results and exports them in formats suitable
for AMRM white papers and compliance documentation.

Usage:
    python export_results.py --dataset certification/financebench-v1
    python export_results.py --dataset certification/financebench-v1 --run-name "my-run"
    python export_results.py --dataset certification/financebench-v1 --format json
    python export_results.py --dataset certification/financebench-v1 --output report.md

Environment variables:
    LANGFUSE_PUBLIC_KEY  (required)
    LANGFUSE_SECRET_KEY  (required)
    LANGFUSE_BASE_URL    (default: https://cloud.langfuse.com)

Prerequisites:
    pip install 'langfuse>=3.0,<4.0'
"""

import argparse
import csv
import io
import json
import os
import sys
from datetime import datetime

try:
    from langfuse import Langfuse
except ImportError:
    print("Error: langfuse package not installed. Run: pip install 'langfuse>=3.0,<4.0'",
          file=sys.stderr)
    sys.exit(1)


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Export certification experiment results from Langfuse"
    )
    parser.add_argument("--dataset", type=str, required=True,
                        help="Langfuse dataset name to query")
    parser.add_argument("--run-name", type=str, default=None,
                        help="Specific run name (default: latest run)")
    parser.add_argument("--format", type=str, default="markdown",
                        choices=["markdown", "json", "csv"],
                        help="Output format (default: markdown)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output file path (default: stdout)")
    return parser.parse_args()


# --------------- Data Collection ---------------

def collect_run_data(client, dataset_name, run_name=None):
    """Collect experiment run data from Langfuse.

    Returns a dict with run metadata and per-item scores.
    """
    dataset = client.get_dataset(dataset_name)

    # Find the target run
    runs = dataset.runs
    if not runs:
        print(f"Error: no runs found for dataset '{dataset_name}'", file=sys.stderr)
        sys.exit(1)

    if run_name:
        target_runs = [r for r in runs if r.name == run_name]
        if not target_runs:
            available = [r.name for r in runs]
            print(f"Error: run '{run_name}' not found. Available: {available}", file=sys.stderr)
            sys.exit(1)
        run = target_runs[0]
    else:
        # Use the most recent run
        run = runs[-1]

    print(f"  Exporting run: {run.name}", file=sys.stderr)

    # Collect scores from run items
    items_data = []
    score_totals = {}  # {score_name: [values]}

    for run_item in run.dataset_run_items:
        trace_id = run_item.trace_id

        # Get scores for this trace
        item_scores = {}
        try:
            trace = client.get_trace(trace_id)
            if hasattr(trace, 'scores') and trace.scores:
                for score in trace.scores:
                    item_scores[score.name] = {
                        "value": score.value,
                        "comment": getattr(score, 'comment', ''),
                    }
                    if score.name not in score_totals:
                        score_totals[score.name] = []
                    if score.value is not None:
                        score_totals[score.name].append(score.value)
        except Exception as e:
            print(f"    Warning: could not fetch trace {trace_id}: {e}", file=sys.stderr)

        # Get the dataset item input/expected for context
        dataset_item = run_item.dataset_item
        items_data.append({
            "trace_id": trace_id,
            "input": dataset_item.input if dataset_item else {},
            "expected_output": dataset_item.expected_output if dataset_item else {},
            "scores": item_scores,
        })

    # Compute aggregates
    aggregates = {}
    for name, values in score_totals.items():
        if values:
            aggregates[name] = {
                "mean": round(sum(values) / len(values), 3),
                "min": round(min(values), 3),
                "max": round(max(values), 3),
                "count": len(values),
                "pass_rate": round(sum(1 for v in values if v >= 0.5) / len(values), 3),
            }

    return {
        "dataset": dataset_name,
        "run_name": run.name,
        "run_metadata": run.metadata or {},
        "exported_at": datetime.now().isoformat(),
        "total_items": len(items_data),
        "aggregates": aggregates,
        "items": items_data,
    }


# --------------- Formatters ---------------

def format_markdown(data: dict) -> str:
    """Format results as a certification report in Markdown."""
    model = data["run_metadata"].get("model", "unknown")
    threshold = data["run_metadata"].get("threshold", 0.85)

    # Determine pass/fail from certification_result if available
    cert = data["aggregates"].get("certification_result", {})
    if cert:
        status = "PASSED" if cert.get("mean", 0) >= 0.5 else "FAILED"
    else:
        status = "REVIEW REQUIRED"

    lines = [
        f"# Model Certification Report",
        f"",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Model** | {model} |",
        f"| **Dataset** | {data['dataset']} |",
        f"| **Run** | {data['run_name']} |",
        f"| **Date** | {data['exported_at'][:10]} |",
        f"| **Items Evaluated** | {data['total_items']} |",
        f"| **Threshold** | {threshold:.0%} |",
        f"| **Result** | **{status}** |",
        f"",
        f"## Scores Summary",
        f"",
        f"| Evaluator | Mean | Min | Max | Count | Pass Rate |",
        f"|-----------|------|-----|-----|-------|-----------|",
    ]

    for name, agg in sorted(data["aggregates"].items()):
        lines.append(
            f"| {name} | {agg['mean']:.3f} | {agg['min']:.3f} | "
            f"{agg['max']:.3f} | {agg['count']} | {agg['pass_rate']:.0%} |"
        )

    lines.extend([
        f"",
        f"## Item Details",
        f"",
    ])

    for i, item in enumerate(data["items"], 1):
        inp = item["input"]
        question = inp.get("question", inp.get("text", str(inp)))[:120]
        lines.append(f"### Item {i}")
        lines.append(f"")
        lines.append(f"**Input:** {question}")
        lines.append(f"")

        exp = item["expected_output"]
        if exp:
            answer = exp.get("answer", exp.get("sentiment", ""))
            if answer:
                lines.append(f"**Expected:** {str(answer)[:200]}")
                lines.append(f"")

        if item["scores"]:
            lines.append(f"| Score | Value | Comment |")
            lines.append(f"|-------|-------|---------|")
            for sname, sdata in item["scores"].items():
                val = f"{sdata['value']:.2f}" if sdata['value'] is not None else "N/A"
                comment = str(sdata.get('comment', ''))[:80]
                lines.append(f"| {sname} | {val} | {comment} |")
            lines.append(f"")

    lines.extend([
        f"---",
        f"",
        f"*Generated by clickhouse-llm-evals-finance export_results.py on {data['exported_at'][:10]}*",
    ])

    return "\n".join(lines)


def format_json(data: dict) -> str:
    """Format results as JSON."""
    return json.dumps(data, indent=2, default=str)


def format_csv(data: dict) -> str:
    """Format results as CSV with one row per item-score pair."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "item_num", "trace_id", "question", "expected_answer",
        "score_name", "score_value", "score_comment",
        "model", "dataset", "run_name"
    ])

    model = data["run_metadata"].get("model", "unknown")

    for i, item in enumerate(data["items"], 1):
        inp = item["input"]
        question = inp.get("question", inp.get("text", ""))
        expected = item["expected_output"]
        answer = expected.get("answer", expected.get("sentiment", "")) if expected else ""

        if item["scores"]:
            for sname, sdata in item["scores"].items():
                writer.writerow([
                    i, item["trace_id"], question[:200], str(answer)[:200],
                    sname, sdata["value"], sdata.get("comment", ""),
                    model, data["dataset"], data["run_name"]
                ])
        else:
            writer.writerow([
                i, item["trace_id"], question[:200], str(answer)[:200],
                "", "", "", model, data["dataset"], data["run_name"]
            ])

    return output.getvalue()


# --------------- Main ---------------

def main():
    args = parse_args()

    # Load .env if available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    host = os.getenv("LANGFUSE_BASE_URL", os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    sk = os.getenv("LANGFUSE_SECRET_KEY")

    if not pk or not sk:
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required", file=sys.stderr)
        sys.exit(1)

    client = Langfuse(public_key=pk, secret_key=sk, host=host)

    print("Langfuse Results Exporter", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Dataset: {args.dataset}", file=sys.stderr)
    print(f"  Format:  {args.format}", file=sys.stderr)

    # Collect data
    data = collect_run_data(client, args.dataset, args.run_name)

    # Format output
    formatters = {
        "markdown": format_markdown,
        "json": format_json,
        "csv": format_csv,
    }
    output = formatters[args.format](data)

    # Write output
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"\n  Written to: {args.output}", file=sys.stderr)
    else:
        print(output)

    print(f"\n{'=' * 50}", file=sys.stderr)
    print("Done.", file=sys.stderr)


if __name__ == "__main__":
    main()
