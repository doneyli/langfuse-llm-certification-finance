#!/usr/bin/env python3
"""Print the number of items in a Langfuse dataset (0 if it is missing or empty).

Used by `make seed` to load golden datasets idempotently: `setup_datasets.py` is
additive (it appends items on every call), so the Makefile only loads a dataset
when this reports 0 items. Mirrors the `dataset_count` guard in
scripts/demo_usecase.sh.

Usage: python scripts/dataset_count.py <dataset-name>
"""
import sys

try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except ImportError:
    pass

try:
    from langfuse import get_client

    print(len(get_client().get_dataset(sys.argv[1]).items))
except Exception:
    print(0)
