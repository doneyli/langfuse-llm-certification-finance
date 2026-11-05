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

Reads the Langfuse v4 API only (GET /experiments, GET /experiment-items).

Prerequisites:
    pip install 'langfuse>=5,<6'
"""

import argparse
import csv
import io
import json
import os
import sys
import urllib.parse
from datetime import datetime

from cert_common import (
    api_get,
    bar_verdict,
    describe_advisory,
    describe_gate,
    lookback_from_env,
    normalize_run_metadata,
    paginate_cursor,
    parse_json_field,
    recorded_advisory,
    recorded_gate,
    score_value,
)


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

RUN_LEVEL_SCORES = {"certification_result"}


def _dataset_id(dataset_name):
    encoded = urllib.parse.quote(dataset_name, safe="")
    return api_get(f"/api/public/v2/datasets/{encoded}").get("id")


def collect_run_data(dataset_name, run_name=None):
    """Collect one gate run (experiment) from the Langfuse v4 read API.

    The run and its run-level scores (``certification_result``, ``avg_*``) come
    from ``GET /experiments``; per-item input, expected output and scores from
    ``GET /experiment-items``. Returns a dict with run metadata, run-level
    scores, per-item scores and aggregates.
    """
    try:
        dataset_id = _dataset_id(dataset_name)
    except Exception as e:
        print(f"Error: could not fetch dataset '{dataset_name}': {e}", file=sys.stderr)
        sys.exit(1)

    params = {"datasetId": dataset_id, "fromStartTime": lookback_from_env(),
              "fields": "core,metadata,scores"}
    if run_name:
        params["name"] = run_name
    runs = paginate_cursor("/api/public/experiments", params)
    if run_name:
        runs = [r for r in runs if r.get("name") == run_name]
        if not runs:
            print(f"Error: run '{run_name}' not found on dataset '{dataset_name}'",
                  file=sys.stderr)
            sys.exit(1)
    else:
        # "Latest run" never means a disposable spike (scripts/spike_span_nesting.py).
        runs = [r for r in runs if not (r.get("name") or "").startswith("spike-")]
    if not runs:
        print(f"Error: no runs found for dataset '{dataset_name}'", file=sys.stderr)
        sys.exit(1)
    run = max(runs, key=lambda r: r.get("startTime", ""))

    print(f"  Exporting run: {run['name']}", file=sys.stderr)

    run_scores = {}
    for s in run.get("scores") or []:
        if s.get("name"):
            run_scores[s["name"]] = {"value": score_value(s),
                                     "comment": s.get("comment") or ""}

    run_items = paginate_cursor("/api/public/experiment-items", {
        "experimentId": run["id"], "fromStartTime": lookback_from_env(),
        "fields": "core,io,scores,itemMetadata",
    })

    items_data = []
    score_totals = {}  # {score_name: [values]}
    for run_item in run_items:
        item_scores = {}
        for score in run_item.get("scores") or []:
            sname = score.get("name")
            # Run-level scores belong to the experiment; skip any copy a
            # pre-v5 runner also wrote onto an item trace.
            if not sname or sname in RUN_LEVEL_SCORES or sname.startswith("avg_"):
                continue
            value = score_value(score)
            item_scores[sname] = {"value": value,
                                  "comment": score.get("comment") or ""}
            score_totals.setdefault(sname, [])
            if value is not None:
                score_totals[sname].append(value)

        inp = parse_json_field(run_item.get("input"))
        expected = parse_json_field(run_item.get("expectedOutput"))
        items_data.append({
            "trace_id": run_item.get("traceId", ""),
            "input": inp if isinstance(inp, dict) else {"text": str(inp or "")},
            "expected_output": expected if isinstance(expected, dict) else {},
            "scores": item_scores,
        })

    # Each score's bar and whether it cleared, from the run's own metadata and
    # the unrounded mean the gate compared (the rounded mean is display-only).
    run_metadata = normalize_run_metadata(run.get("metadata"))
    means = {name: sum(values) / len(values)
             for name, values in score_totals.items() if values}
    required = recorded_gate(run_metadata, means) or {}
    advisory = recorded_advisory(run_metadata) or {}

    aggregates = {}
    for name, values in score_totals.items():
        if values:
            aggregates[name] = {
                "mean": round(means[name], 3),
                "min": round(min(values), 3),
                "max": round(max(values), 3),
                "count": len(values),
                "pass_rate": round(sum(1 for v in values if v >= 0.5) / len(values), 3),
                **bar_verdict(name, means[name], required, advisory),
            }

    return {
        "dataset": dataset_name,
        "run_name": run["name"],
        "experiment_id": run.get("id"),
        "run_metadata": run_metadata,
        "run_scores": run_scores,
        "exported_at": datetime.now().isoformat(),
        "total_items": len(items_data),
        "aggregates": aggregates,
        "items": items_data,
    }


# --------------- Formatters ---------------

def _bar_cell(agg: dict) -> str:
    """'≥ 80% PASS', '≥ 70% MISS (advisory)', or '—' when the gate judged nothing.

    Required bars read PASS/FAIL and advisory bars MET/MISS, the same words the
    certification_result comment uses, so the two never disagree.
    """
    bar = agg.get("bar")
    if bar is None:
        return "—"
    cleared = agg.get("cleared")
    if agg.get("advisory"):
        word = "MET" if cleared else "MISS"
        return f"≥ {bar:.0%} {word} (advisory)"
    return f"≥ {bar:.0%} {'PASS' if cleared else 'FAIL'}"


def format_markdown(data: dict) -> str:
    """Format results as a certification report in Markdown."""
    model = data["run_metadata"].get("model", "unknown")
    # The bars this run was actually judged by, from its own metadata — an agent
    # run records one per dimension, and a run that recorded none says so
    # rather than being reported against a bar nothing enforced.
    gate = recorded_gate(data["run_metadata"], data["aggregates"])
    # Advisory bars were reported but never decided the result, so they get a
    # row of their own rather than joining the "all must clear" gate.
    advisory = recorded_advisory(data["run_metadata"])

    # Pass/fail is the run's own certification_result score (on the experiment).
    cert = (data.get("run_scores") or {}).get("certification_result") or {}
    if cert.get("value") is not None:
        status = "PASSED" if cert["value"] >= 0.5 else "FAILED"
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
        f"| **Experiment ID** | {data.get('experiment_id') or '—'} |",
        f"| **Date** | {data['exported_at'][:10]} |",
        f"| **Items Evaluated** | {data['total_items']} |",
        f"| **Gate** | {describe_gate(gate)} |",
        *([f"| **Advisory** | {describe_advisory(advisory)} |"] if advisory else []),
        f"| **Result** | **{status}** |",
        f"",
        f"## Scores Summary",
        f"",
        f"| Evaluator | Bar | Mean | Min | Max | Count | Pass Rate |",
        f"|-----------|-----|------|-----|-----|-------|-----------|",
    ]

    for name, agg in sorted(data["aggregates"].items()):
        lines.append(
            f"| {name} | {_bar_cell(agg)} | {agg['mean']:.3f} | {agg['min']:.3f} | "
            f"{agg['max']:.3f} | {agg['count']} | {agg['pass_rate']:.0%} |"
        )

    run_scores = data.get("run_scores") or {}
    if run_scores:
        lines.extend([
            f"",
            f"## Run-Level Scores",
            f"",
            f"| Score | Value | Comment |",
            f"|-------|-------|---------|",
        ])
        for name, sdata in sorted(run_scores.items()):
            val = f"{sdata['value']:.3f}" if sdata["value"] is not None else "N/A"
            lines.append(f"| {name} | {val} | {str(sdata.get('comment', ''))[:120]} |")

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
        f"*Generated by langfuse-llm-certification-finance export_results.py on {data['exported_at'][:10]}*",
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
        load_dotenv(override=True)
    except ImportError:
        pass

    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    sk = os.getenv("LANGFUSE_SECRET_KEY")

    if not pk or not sk:
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required", file=sys.stderr)
        sys.exit(1)

    print("Langfuse Results Exporter", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Dataset: {args.dataset}", file=sys.stderr)
    print(f"  Format:  {args.format}", file=sys.stderr)

    # Collect data
    data = collect_run_data(args.dataset, args.run_name)

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
