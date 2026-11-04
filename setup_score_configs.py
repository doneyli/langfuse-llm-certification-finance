#!/usr/bin/env python3
"""
Create Score Configs in Langfuse

Registers score configurations for all evaluators so that scores are validated,
have proper types/ranges, and display with descriptions in the Langfuse UI.

Pass/fail scores (the gate verdict ``certification_result`` and the binary
item checks) are BOOLEAN, which unlocks boolean dashboard widgets, alerts on the
true-rate, and Score Analytics confusion matrices. A config's dataType cannot be
changed, so a project that already registered them as NUMERIC is migrated only
on request: ``--migrate-configs`` archives the old config and creates the
BOOLEAN one (scores already written keep their old type).

Usage:
    python setup_score_configs.py                           # Create missing configs
    python setup_score_configs.py --dry-run                 # Preview, incl. type mismatches
    python setup_score_configs.py --migrate-configs --dry-run
    python setup_score_configs.py --migrate-configs         # Archive NUMERIC -> create BOOLEAN

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
        "dataType": "BOOLEAN",
        "description": "Whether the model's numerical answer matches the expected value "
                       "within 5% tolerance. true = match, false = mismatch.",
    },
    {
        "name": "exact_match",
        "dataType": "BOOLEAN",
        "description": "Whether the expected answer string appears verbatim in the model "
                       "output. true = found, false = not found.",
    },
    {
        "name": "sentiment_accuracy",
        "dataType": "BOOLEAN",
        "description": "Whether the model correctly classified the financial text sentiment "
                       "(positive/negative/neutral). true = correct, false = incorrect.",
    },
    {
        "name": "regulatory_compliance",
        "dataType": "BOOLEAN",
        "description": "Whether the model output is free of prohibited financial phrases "
                       "(e.g., 'guaranteed returns', 'risk-free'). true = clean, false = violation.",
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
    # Trajectory evaluator (use-case / agent certification)
    {
        "name": "tool_use_correctness",
        "dataType": "BOOLEAN",
        "description": "Use-case certification: whether the agent's trajectory matched "
                       "the question type (e.g. used the calculator tool for "
                       "numerical-reasoning questions). true = correct path, false = "
                       "required tool not invoked.",
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
        "name": "avg_regulatory_compliance",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average regulatory_compliance across all items in the run "
                       "(use-case certification gate dimension).",
    },
    {
        "name": "avg_completeness",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average completeness across all items in the run "
                       "(use-case certification gate dimension).",
    },
    {
        "name": "avg_tool_use_correctness",
        "dataType": "NUMERIC",
        "minValue": 0,
        "maxValue": 1,
        "description": "Average tool_use_correctness across all agent items in the run "
                       "(use-case certification gate dimension).",
    },
    {
        "name": "certification_result",
        "dataType": "BOOLEAN",
        "description": "Deployment gate verdict (run-level, on the experiment): "
                       "true = PASSED (every gated score met its bar in "
                       "cicd/thresholds.json), false = FAILED.",
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
    """Fetch existing, non-archived score configs as a {name: config} dict."""
    out, page = {}, 1
    while True:
        req = urllib.request.Request(
            f"{host}/api/public/score-configs?limit=100&page={page}",
            headers={"Authorization": f"Basic {auth}"},
        )
        data = json.loads(urllib.request.urlopen(req).read())
        for cfg in data.get("data", []):
            if not cfg.get("isArchived"):
                out[cfg["name"]] = cfg
        if page >= ((data.get("meta") or {}).get("totalPages") or 1):
            return out
        page += 1


def plan_config_changes(existing: dict, desired: list) -> list:
    """Decide per desired config: ``create``, ``skip`` or ``migrate``.

    ``migrate`` means a live config of that name exists with a different
    dataType — it cannot be changed in place (Langfuse rejects dataType
    edits), so it must be archived and recreated. Returns
    ``[(action, desired_cfg, existing_cfg_or_None)]``.
    """
    plan = []
    for cfg in desired:
        current = existing.get(cfg["name"])
        if current is None:
            plan.append(("create", cfg, None))
        elif current.get("dataType") != cfg["dataType"]:
            plan.append(("migrate", cfg, current))
        else:
            plan.append(("skip", cfg, current))
    return plan


def archive_config(host: str, auth: str, config_id: str) -> dict:
    """Archive a score config (PATCH isArchived=true). Scores keep their data."""
    req = urllib.request.Request(
        f"{host}/api/public/score-configs/{config_id}",
        data=json.dumps({"isArchived": True}).encode(),
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/json"},
        method="PATCH",
    )
    return json.loads(urllib.request.urlopen(req).read())


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
    parser.add_argument("--migrate-configs", action="store_true",
                        help="For configs registered with a different dataType "
                             "(e.g. NUMERIC -> BOOLEAN): archive the old config "
                             "and create the new one")
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
        print("\n  ** DRY RUN - no configs will be created or archived **\n",
              file=sys.stderr)
        for cfg in SCORE_CONFIGS:
            dtype = cfg["dataType"]
            range_str = ""
            if "minValue" in cfg and "maxValue" in cfg:
                range_str = f" [{cfg['minValue']}-{cfg['maxValue']}]"
            print(f"  {cfg['name']:30s} {dtype}{range_str}", file=sys.stderr)
            print(f"    {cfg['description'][:80]}...", file=sys.stderr)

    # Check existing configs to avoid duplicates and spot type mismatches
    existing = list_existing_configs(host, auth)
    print(f"\n  Existing: {len(existing)} configs\n", file=sys.stderr)

    created = skipped = migrated = mismatched = 0
    for action, cfg, current in plan_config_changes(existing, SCORE_CONFIGS):
        name = cfg["name"]
        if action == "skip":
            print(f"  [skip] {name} (already exists)", file=sys.stderr)
            skipped += 1
            continue
        if action == "migrate":
            if not args.migrate_configs:
                print(f"  [mismatch] {name}: registered {current['dataType']}, "
                      f"wanted {cfg['dataType']} — rerun with --migrate-configs "
                      f"to archive {current['id']} and create a "
                      f"{cfg['dataType']} config", file=sys.stderr)
                mismatched += 1
                continue
            if args.dry_run:
                print(f"  [would migrate] {name}: archive {current['dataType']} "
                      f"config {current['id']}, create {cfg['dataType']}",
                      file=sys.stderr)
                migrated += 1
                continue
            try:
                archive_config(host, auth, current["id"])
                print(f"  [archived] {name} ({current['dataType']}, id: "
                      f"{current['id']})", file=sys.stderr)
            except urllib.error.HTTPError as e:
                print(f"  [error] archiving {name}: {e.code} {e.read().decode()}",
                      file=sys.stderr)
                continue
        if args.dry_run:
            print(f"  [would create] {name} ({cfg['dataType']})", file=sys.stderr)
            created += 1
            continue
        try:
            result = create_config(host, auth, cfg)
            print(f"  [created] {name} ({cfg['dataType']}, id: {result['id']})",
                  file=sys.stderr)
            if action == "migrate":
                migrated += 1
            else:
                created += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  [error] {name}: {e.code} {body}", file=sys.stderr)

    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Created:  {created}", file=sys.stderr)
    print(f"  Migrated: {migrated}", file=sys.stderr)
    print(f"  Skipped:  {skipped} (already existed)", file=sys.stderr)
    if mismatched:
        print(f"  Type mismatches left as-is: {mismatched} "
              f"(see --migrate-configs)", file=sys.stderr)
    print(f"\nVerify in Langfuse UI: Settings > Score Configs", file=sys.stderr)


if __name__ == "__main__":
    main()
