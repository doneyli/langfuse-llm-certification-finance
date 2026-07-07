#!/usr/bin/env python3
"""
Load Financial Evaluation Datasets into Langfuse

Creates Langfuse datasets from open-source financial benchmarks for
LLM model certification. Supports offline mode with embedded sample data.

Available datasets:
  - financebench  (10 sample / 150 full) - Financial QA from SEC filings
  - fpb           (10 sample / ~4850 full) - Financial sentiment classification

Usage:
    python setup_datasets.py                                # Load all (full)
    python setup_datasets.py --dataset financebench         # Only FinanceBench
    python setup_datasets.py --dataset fpb --sample         # FPB sample data
    python setup_datasets.py --dry-run                      # Preview without creating
    python setup_datasets.py --prefix acme/certification    # Custom dataset prefix

Environment variables:
    LANGFUSE_PUBLIC_KEY  (required)
    LANGFUSE_SECRET_KEY  (required)
    LANGFUSE_BASE_URL    (default: https://cloud.langfuse.com)

Prerequisites:
    pip install 'langfuse>=3.0,<4.0'
    pip install datasets          # only needed without --sample
"""

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from langfuse import Langfuse
except ImportError:
    print("Error: langfuse package not installed. Run: pip install 'langfuse>=3.0,<4.0'",
          file=sys.stderr)
    sys.exit(1)


SCRIPT_DIR = Path(__file__).parent
SAMPLE_DIR = SCRIPT_DIR / "sample_data"


# --------------- Dataset Definitions ---------------

def load_financebench_items(sample: bool):
    """Load FinanceBench items from sample file or HuggingFace."""
    if sample:
        path = SAMPLE_DIR / "financebench_sample.json"
        with open(path) as f:
            raw = json.load(f)
        print(f"  Loaded {len(raw)} items from {path.name}", file=sys.stderr)
    else:
        try:
            from datasets import load_dataset
        except ImportError:
            print("Error: 'datasets' package required for full download. "
                  "Run: pip install datasets\n"
                  "Or use --sample for offline mode.", file=sys.stderr)
            sys.exit(1)
        ds = load_dataset("PatronusAI/financebench", split="train")
        raw = [dict(item) for item in ds]
        print(f"  Downloaded {len(raw)} items from HuggingFace", file=sys.stderr)

    items = []
    for item in raw:
        # Extract evidence text from the evidence field (list of dicts or strings)
        evidence_texts = []
        for ev in item.get("evidence", []):
            if isinstance(ev, dict):
                evidence_texts.append(ev.get("evidence_text", ""))
            elif isinstance(ev, str):
                evidence_texts.append(ev)

        items.append({
            "input": {
                "question": item["question"],
                "company": item.get("company", ""),
                "doc_type": item.get("doc_type", ""),
                "doc_link": item.get("doc_link", ""),
                "evidence": evidence_texts,
            },
            "expected_output": {
                "answer": item["answer"],
                "justification": item.get("justification", ""),
            },
            "metadata": {
                "question_type": item.get("question_type", ""),
                "question_reasoning": item.get("question_reasoning", ""),
                "gics_sector": item.get("gics_sector", ""),
                "financebench_id": item.get("financebench_id", ""),
                "source": "PatronusAI/financebench",
            },
        })
    return items


def load_fpb_items(sample: bool):
    """Load Financial PhraseBank items from sample file or HuggingFace."""
    if sample:
        path = SAMPLE_DIR / "fpb_sample.json"
        with open(path) as f:
            raw = json.load(f)
        print(f"  Loaded {len(raw)} items from {path.name}", file=sys.stderr)
    else:
        try:
            from datasets import load_dataset
        except ImportError:
            print("Error: 'datasets' package required for full download. "
                  "Run: pip install datasets\n"
                  "Or use --sample for offline mode.", file=sys.stderr)
            sys.exit(1)
        ds = load_dataset("ChanceFocus/en-fpb", split="test")
        raw = [dict(item) for item in ds]
        print(f"  Downloaded {len(raw)} items from HuggingFace", file=sys.stderr)

    label_map = {0: "negative", 1: "neutral", 2: "positive"}

    items = []
    for item in raw:
        # Sample data uses string labels; HuggingFace uses int labels
        label = item.get("label", item.get("gold", ""))
        if isinstance(label, int):
            label = label_map.get(label, str(label))

        items.append({
            "input": {
                "text": item.get("text", item.get("sentence", "")),
            },
            "expected_output": {
                "sentiment": label,
            },
            "metadata": {
                "source": "ChanceFocus/en-fpb",
            },
        })
    return items


def load_advisory_adversarial_items():
    """Load the small Advisory-Drafting demo set from the local JSON file.

    This set exists to demonstrate the compliance *gate* (issue #11): a control
    item that should draft cleanly, and an adversarial item whose framing tempts
    prohibited "guaranteed / risk-free" language. It is always read from
    sample_data/advisory_adversarial.json (never HuggingFace) so it is small,
    reproducible, and auditable.
    """
    path = SAMPLE_DIR / "advisory_adversarial.json"
    with open(path) as f:
        raw = json.load(f)
    print(f"  Loaded {len(raw)} items from {path.name}", file=sys.stderr)
    # Already in {input, expected_output, metadata} shape.
    return raw


# --------------- Dataset Creation ---------------

def create_dataset(client, name, description, items, dry_run=False):
    """Create a Langfuse dataset and populate with items."""
    print(f"\n  Dataset: {name}", file=sys.stderr)
    print(f"  Description: {description}", file=sys.stderr)
    print(f"  Items: {len(items)}", file=sys.stderr)

    if dry_run:
        for i, item in enumerate(items[:5]):
            preview = str(item["input"])[:80]
            print(f"    [{i+1}] {preview}...", file=sys.stderr)
        if len(items) > 5:
            print(f"    ... and {len(items) - 5} more", file=sys.stderr)
        return

    # Create or get dataset
    try:
        client.create_dataset(
            name=name,
            description=description,
            metadata={"created_by": "setup_datasets.py"}
        )
        print(f"  Created dataset: {name}", file=sys.stderr)
    except Exception as e:
        if "already exists" in str(e).lower() or "409" in str(e):
            print(f"  Dataset already exists: {name} (adding items)", file=sys.stderr)
        else:
            print(f"  Warning creating dataset: {e}", file=sys.stderr)

    # Add items
    created = 0
    for item in items:
        try:
            client.create_dataset_item(
                dataset_name=name,
                input=item["input"],
                expected_output=item["expected_output"],
                metadata=item["metadata"],
            )
            created += 1
        except Exception as e:
            print(f"    Error adding item: {e}", file=sys.stderr)

    print(f"  Added {created}/{len(items)} items", file=sys.stderr)


# --------------- CLI ---------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Load financial evaluation datasets into Langfuse"
    )
    parser.add_argument("--dataset",
                        choices=["financebench", "fpb", "advisory-adversarial", "all"],
                        default="all", help="Which dataset(s) to load (default: all). "
                        "'advisory-adversarial' is the small compliance-gate demo set "
                        "(issue #11) and is not included in 'all'.")
    parser.add_argument("--sample", action="store_true",
                        help="Use embedded sample data instead of downloading from HuggingFace")
    parser.add_argument("--prefix", type=str, default="certification",
                        help="Dataset name prefix (default: certification)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview items without creating in Langfuse")
    return parser.parse_args()


# --------------- Main ---------------

def main():
    args = parse_args()

    # Load .env if available
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass  # python-dotenv is optional

    host = os.getenv("LANGFUSE_BASE_URL", os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))

    print("Langfuse Dataset Loader", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target:  {host}", file=sys.stderr)
    print(f"  Prefix:  {args.prefix}", file=sys.stderr)
    print(f"  Source:  {'sample data' if args.sample else 'HuggingFace'}", file=sys.stderr)

    if args.dry_run:
        print("\n  ** DRY RUN - no data will be created **", file=sys.stderr)
        client = None
    else:
        pk = os.getenv("LANGFUSE_PUBLIC_KEY")
        sk = os.getenv("LANGFUSE_SECRET_KEY")
        if not pk or not sk:
            print("Error: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY required", file=sys.stderr)
            sys.exit(1)
        client = Langfuse(public_key=pk, secret_key=sk, host=host)

    suffix = "-sample" if args.sample else "-v1"

    if args.dataset in ("financebench", "all"):
        items = load_financebench_items(args.sample)
        create_dataset(
            client,
            name=f"{args.prefix}/financebench{suffix}",
            description="FinanceBench - Financial QA from SEC filings "
                        "(extraction, numerical reasoning, logical reasoning). "
                        "Source: PatronusAI/financebench (CC-BY-NC-4.0).",
            items=items,
            dry_run=args.dry_run,
        )

    if args.dataset in ("fpb", "all"):
        items = load_fpb_items(args.sample)
        create_dataset(
            client,
            name=f"{args.prefix}/fpb{suffix}",
            description="Financial PhraseBank - Sentiment classification of financial text "
                        "(positive/negative/neutral). Source: ChanceFocus/en-fpb.",
            items=items,
            dry_run=args.dry_run,
        )

    if args.dataset == "advisory-adversarial":
        items = load_advisory_adversarial_items()
        create_dataset(
            client,
            name=f"{args.prefix}/advisory-adversarial",
            description="Advisory Drafting compliance-gate demo (issue #11): a control "
                        "item plus an adversarial item that tempts prohibited "
                        "'guaranteed / risk-free' language. Run with the advisory-draft "
                        "agent; set ADVISORY_TEMPT_NONCOMPLIANT=1 to force the FAIL path.",
            items=items,
            dry_run=args.dry_run,
        )

    if not args.dry_run and client:
        client.flush()

    print(f"\n{'=' * 50}", file=sys.stderr)
    print("Done.", file=sys.stderr)
    if not args.dry_run:
        print(f"\nVerify in Langfuse UI: {host}", file=sys.stderr)
        print("  Navigate to: Datasets in the left sidebar", file=sys.stderr)


if __name__ == "__main__":
    main()
