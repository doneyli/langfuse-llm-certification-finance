#!/usr/bin/env python3
"""
Create Annotation Queues in Langfuse

Sets up annotation queues for human review of certification experiment results.
Queues are linked to human review score configs created by setup_score_configs.py.

Usage:
    python setup_annotation_queues.py              # Create queues
    python setup_annotation_queues.py --dry-run    # Preview without creating

Prerequisites:
    Run setup_score_configs.py first to create the human review score configs.

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


# --------------- Queue Definitions ---------------

# Maps queue name -> list of score config names it should use
ANNOTATION_QUEUES = [
    {
        "name": "Certification Review",
        "description": "Review failed or low-confidence certification items. "
                       "Route here: items where numerical_accuracy=0, "
                       "groundedness<0.5, or any certification failure.",
        "score_config_names": ["human_accuracy", "human_groundedness"],
    },
]


# --------------- API Helpers ---------------

def get_auth_header(public_key: str, secret_key: str) -> str:
    return base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()


def api_get(host: str, auth: str, path: str) -> dict:
    req = urllib.request.Request(
        f"{host}{path}",
        headers={"Authorization": f"Basic {auth}"},
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())


def api_post(host: str, auth: str, path: str, body: dict) -> dict:
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


def get_score_configs(host: str, auth: str) -> dict:
    """Fetch score configs and return as {name: config} dict."""
    data = api_get(host, auth, "/api/public/score-configs?limit=100")
    return {cfg["name"]: cfg for cfg in data.get("data", [])}


def get_existing_queues(host: str, auth: str) -> dict:
    """Fetch annotation queues and return as {name: queue} dict."""
    data = api_get(host, auth, "/api/public/annotation-queues?limit=100")
    return {q["name"]: q for q in data.get("data", [])}


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Create annotation queues in Langfuse for human review"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview queues without creating them")
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

    print("Langfuse Annotation Queue Setup", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target: {host}", file=sys.stderr)

    # Resolve score config names -> IDs
    score_configs = get_score_configs(host, auth)
    print(f"  Score configs found: {len(score_configs)}", file=sys.stderr)

    if args.dry_run:
        print("\n  ** DRY RUN - no queues will be created **\n", file=sys.stderr)
        for q in ANNOTATION_QUEUES:
            print(f"  Queue: {q['name']}", file=sys.stderr)
            print(f"    {q['description'][:80]}...", file=sys.stderr)
            for sc_name in q["score_config_names"]:
                status = "found" if sc_name in score_configs else "MISSING"
                print(f"    Score config: {sc_name} ({status})", file=sys.stderr)
        return

    existing_queues = get_existing_queues(host, auth)
    print(f"  Existing queues: {len(existing_queues)}\n", file=sys.stderr)

    created = 0
    skipped = 0
    for q_def in ANNOTATION_QUEUES:
        if q_def["name"] in existing_queues:
            print(f"  [skip] {q_def['name']} (already exists)", file=sys.stderr)
            skipped += 1
            continue

        # Resolve score config IDs
        config_ids = []
        missing = []
        for sc_name in q_def["score_config_names"]:
            if sc_name in score_configs:
                config_ids.append(score_configs[sc_name]["id"])
            else:
                missing.append(sc_name)

        if missing:
            print(f"  [error] {q_def['name']}: missing score configs: {missing}",
                  file=sys.stderr)
            print(f"    Run setup_score_configs.py first.", file=sys.stderr)
            continue

        try:
            result = api_post(host, auth, "/api/public/annotation-queues", {
                "name": q_def["name"],
                "description": q_def["description"],
                "scoreConfigIds": config_ids,
            })
            print(f"  [created] {q_def['name']} (id: {result['id']})",
                  file=sys.stderr)
            created += 1
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  [error] {q_def['name']}: {e.code} {body}", file=sys.stderr)

    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Created: {created}", file=sys.stderr)
    print(f"  Skipped: {skipped}", file=sys.stderr)
    print(f"\nView in Langfuse UI: Annotation Queues (left sidebar)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
