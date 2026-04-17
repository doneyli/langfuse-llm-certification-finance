#!/usr/bin/env python3
"""
Create Prompt Templates in Langfuse

Registers the certification prompt templates in Langfuse prompt management.
Once created, prompts can be edited and versioned in the Langfuse UI without
code changes. The experiment runner fetches the 'production'-labeled version.

Usage:
    python setup_prompts.py              # Create prompts
    python setup_prompts.py --dry-run    # Preview without creating

Environment variables:
    LANGFUSE_PUBLIC_KEY  (required)
    LANGFUSE_SECRET_KEY  (required)
    LANGFUSE_BASE_URL    (default: https://cloud.langfuse.com)
"""

import argparse
import os
import sys

try:
    from langfuse import Langfuse
except ImportError:
    print("Error: langfuse package not installed. Run: pip install 'langfuse>=3.0,<4.0'",
          file=sys.stderr)
    sys.exit(1)


# --------------- Prompt Definitions ---------------

PROMPTS = [
    {
        "name": "financial-qa",
        "type": "text",
        "prompt": (
            "You are a financial analyst. Answer the question using ONLY the "
            "provided source document excerpts. Be precise with numbers.\n\n"
            "{{evidence}}\n\n"
            "--- Question ---\n{{question}}"
        ),
        "labels": ["production"],
        "tags": ["certification", "financebench"],
        "config": {
            "description": "System prompt for financial QA with filing evidence. "
                           "Used by FinanceBench certification experiments.",
            "variables": ["evidence", "question"],
        },
    },
    {
        "name": "financial-sentiment",
        "type": "text",
        "prompt": (
            "You are a financial analyst. Classify the sentiment of the following "
            "financial text as exactly one of: positive, negative, or neutral.\n\n"
            "Text: {{text}}\n\n"
            "Respond with only the sentiment label."
        ),
        "labels": ["production"],
        "tags": ["certification", "fpb"],
        "config": {
            "description": "Sentiment classification prompt for Financial PhraseBank. "
                           "Used by FPB certification experiments.",
            "variables": ["text"],
        },
    },
]


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Create prompt templates in Langfuse for certification experiments"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview prompts without creating them")
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

    print("Langfuse Prompt Setup", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target:  {host}", file=sys.stderr)
    print(f"  Prompts: {len(PROMPTS)}", file=sys.stderr)

    if args.dry_run:
        print("\n  ** DRY RUN - no prompts will be created **\n", file=sys.stderr)
        for p in PROMPTS:
            print(f"  {p['name']} ({p['type']})", file=sys.stderr)
            print(f"    Labels: {p['labels']}", file=sys.stderr)
            print(f"    Tags:   {p['tags']}", file=sys.stderr)
            preview = p["prompt"][:80].replace("\n", "\\n")
            print(f"    Prompt: {preview}...", file=sys.stderr)
        return

    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    sk = os.getenv("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required",
              file=sys.stderr)
        sys.exit(1)

    client = Langfuse(public_key=pk, secret_key=sk, host=host)

    created = 0
    skipped = 0
    for p in PROMPTS:
        # Check if prompt already exists with production label
        try:
            existing = client.get_prompt(p["name"], label="production", type=p["type"])
            if existing:
                print(f"  [skip] {p['name']} (already exists, version {existing.version})",
                      file=sys.stderr)
                skipped += 1
                continue
        except Exception:
            pass  # Prompt doesn't exist yet

        try:
            result = client.create_prompt(
                name=p["name"],
                type=p["type"],
                prompt=p["prompt"],
                labels=p["labels"],
                tags=p["tags"],
                config=p["config"],
            )
            print(f"  [created] {p['name']} (version {result.version}, "
                  f"labels: {p['labels']})", file=sys.stderr)
            created += 1
        except Exception as e:
            print(f"  [error] {p['name']}: {e}", file=sys.stderr)

    client.flush()

    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Created: {created}", file=sys.stderr)
    print(f"  Skipped: {skipped}", file=sys.stderr)
    print(f"\nManage prompts in Langfuse UI: Prompts (left sidebar)",
          file=sys.stderr)


if __name__ == "__main__":
    main()
