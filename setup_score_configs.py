#!/usr/bin/env python3
"""
Create Score Configs in Langfuse

Registers score configurations for all evaluators so that scores are validated,
have proper types/ranges, and display with descriptions in the Langfuse UI.

Usage:
    python setup_score_configs.py              # Create all score configs
    python setup_score_configs.py --dry-run    # Preview without creating

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


# --------------- Score Config Definitions ---------------

SCORE_CONFIGS = [
    # Deterministic item-level evaluators
    {
        "name": "numerical_accuracy",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Whether the model's numerical answer matches the expected value "
                       "within 5% tolerance. 1.0 = match, 0.0 = mismatch.",
    },
    {
        "name": "exact_match",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Whether the expected answer string appears verbatim in the model "
                       "output. 1.0 = found, 0.0 = not found.",
    },
    {
        "name": "sentiment_accuracy",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Whether the model correctly classified the financial text sentiment "
                       "(positive/negative/neutral). 1.0 = correct, 0.0 = incorrect.",
    },
    {
        "name": "regulatory_compliance",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Whether the model output is free of prohibited financial phrases "
                       "(e.g., 'guaranteed returns', 'risk-free'). 1.0 = clean, 0.0 = violation.",
    },
    {
        "name": "completeness",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Response quality based on length and structure. Heuristic score "
                       "from 0.2 (very short) to 1.0 (comprehensive with formatting).",
    },
    # LLM-as-a-Judge item-level evaluator
    {
        "name": "groundedness",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "LLM-as-a-Judge: weighted score of faithfulness (70%) and "
                       "completeness (30%) relative to source filing evidence. "
                       "Only scored for items with evidence documents.",
    },
    # Run-level evaluators (aggregated)
    {
        "name": "avg_numerical_accuracy",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average numerical_accuracy across all items in the experiment run.",
    },
    {
        "name": "avg_sentiment_accuracy",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average sentiment_accuracy across all items in the experiment run.",
    },
    {
        "name": "avg_groundedness",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average groundedness across all items with source evidence.",
    },
    {
        "name": "certification_result",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Certification gate: 1.0 = PASSED (primary score meets threshold), "
                       "0.0 = FAILED. Threshold is configurable (default: 85%).",
    },
    # Human review score configs (for Annotation Queues)
    {
        "name": "human_accuracy",
        "dataType": "CATEGORICAL",
        "categories": [
            {"label": "Correct", "value": 1},
            {"label": "Partially Correct", "value": 0.5},
            {"label": "Incorrect", "value": 0},
        ],
        "description": "Human reviewer assessment of answer correctness. Used in "
                       "annotation queues for compliance review and evaluator calibration.",
    },
    {
        "name": "human_groundedness",
        "dataType": "CATEGORICAL",
        "categories": [
            {"label": "Fully Grounded", "value": 1},
            {"label": "Partially Grounded", "value": 0.5},
            {"label": "Not Grounded", "value": 0},
        ],
        "description": "Human reviewer assessment of whether the answer is supported "
                       "by the provided source documents. Used to calibrate the "
                       "LLM-as-a-Judge groundedness evaluator.",
    },
]


# --------------- API Helpers ---------------

def get_auth_header(public_key: str, secret_key: str) -> str:
    return base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def list_existing_configs(host: str, auth: str) -> dict:
    """Fetch existing score configs and return as {name: config} dict."""
    req = urllib.request.Request(
        f"{host}/api/public/score-configs?limit=100",
        headers={"Authorization": f"Basic {auth}"},
    )
    resp = urllib.request.urlopen(req)
    data = json.loads(resp.read())
    return {cfg["name"]: cfg for cfg in data.get("data", [])}


def create_config(host: str, auth: str, config: dict) -> dict:
    """Create a score config via the Langfuse API."""
    body = json.dumps(config).encode()
    req = urllib.request.Request(
        f"{host}/api/public/score-configs",
        data=body,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Create score configs in Langfuse for all evaluators"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview configs without creating them")
    return parser.parse_args()


# --------------- Main ---------------

def main():
    args = parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    host = os.getenv("LANGFUSE_BASE_URL",
                     os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")

    if not pk or not sk:
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required",
              file=sys.stderr)
        sys.exit(1)

    auth = get_auth_header(pk, sk)

    print("Langfuse Score Config Setup", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target:  {host}", file=sys.stderr)
    print(f"  Configs: {len(SCORE_CONFIGS)}", file=sys.stderr)

    if args.dry_run:
        print("\n  ** DRY RUN - no configs will be created **\n", file=sys.stderr)
        for cfg in SCORE_CONFIGS:
            dtype = cfg["dataType"]
            range_str = ""
            if "minValue" in cfg and "maxValue" in cfg:
                range_str = f" [{cfg['minValue']}-{cfg['maxValue']}]"
            print(f"  {cfg['name']:30s} {dtype}{range_str}", file=sys.stderr)
            print(f"    {cfg['description'][:80]}...", file=sys.stderr)
        return

    # Check existing configs to avoid duplicates
    existing = list_existing_configs(host, auth)
    print(f"  Existing: {len(existing)} configs\n", file=sys.stderr)

    created = 0
    skipped = 0
    for cfg in SCORE_CONFIGS:
        if cfg["name"] in existing:
            print(f"  [skip] {cfg['name']} (already exists)", file=sys.stderr)
            skipped += 1
            continue

        try:
            result = create_config(host, auth, cfg)
            print(f"  [created] {cfg['name']} (id: {result['id']})",
                  file=sys.stderr)
            created += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  [error] {cfg['name']}: {e.code} {body}", file=sys.stderr)

    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Created: {created}", file=sys.stderr)
    print(f"  Skipped: {skipped} (already existed)", file=sys.stderr)
    print(f"\nVerify in Langfuse UI: Settings > Score Configs", file=sys.stderr)


if __name__ == "__main__":
    main()
