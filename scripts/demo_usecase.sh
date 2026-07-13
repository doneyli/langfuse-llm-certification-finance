#!/usr/bin/env bash
#
# Use-Case Certification — end-to-end demo / runbook.
#
# Walks the full eval lifecycle: datasets -> prompts -> score configs ->
# annotation queues -> use-case certification run -> where to inspect results.
#
# All three use-case agents (10k-analyst, sentiment-triage, advisory-draft)
# are implemented and registered; pick one via USE_CASE below.
#
# Usage:
#   bash scripts/demo_usecase.sh                 # default: 10k-analyst on financebench-sample
#   USE_CASE=sentiment-triage DATASET=certification/fpb-sample bash scripts/demo_usecase.sh
#   MODEL=claude-haiku-4-5-20251001 bash scripts/demo_usecase.sh   # whole-system lift: cheap model still passes
#
# Env: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL, ANTHROPIC_API_KEY
set -euo pipefail

USE_CASE="${USE_CASE:-10k-analyst}"
DATASET="${DATASET:-certification/financebench-sample}"
MODEL="${MODEL:-claude-sonnet-4-6}"

RUN() { echo; echo "▶ $*"; "$@"; }
banner() { echo; echo "════════════════════════════════════════════════════════════"; echo "  $*"; echo "════════════════════════════════════════════════════════════"; }

cd "$(dirname "$0")/.."

banner "Use-Case Certification Demo  (use_case=$USE_CASE  model=$MODEL)"
echo "Dataset: $DATASET"

# ── Stage 1: golden data ───────────────────────────────────────────────────
# setup_datasets.py is ADDITIVE (creates new items on every call), so loading
# unconditionally would duplicate items on repeated demos. Load only if empty.
banner "1/6  Datasets (golden data)"
dataset_count() {
  uv run python - "$1" <<'PY'
import sys
from dotenv import load_dotenv; load_dotenv(override=True)
from langfuse import get_client
try:
    print(len(get_client().get_dataset(sys.argv[1]).items))
except Exception:
    print(0)
PY
}
load_if_empty() {  # $1 = --dataset arg, $2 = full dataset name
  local n; n="$(dataset_count "$2" | tail -1)"
  if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
    echo "  [skip] $2 already has $n items (additive loader; not reloading)"
  else
    RUN uv run python setup_datasets.py --dataset "$1" --sample
  fi
}
load_if_empty financebench certification/financebench-sample
load_if_empty fpb certification/fpb-sample

# ── Stage 2: prompt management ─────────────────────────────────────────────
banner "2/6  Prompt management (model-cert + use-case agent templates)"
RUN uv run python setup_prompts.py

# ── Stage 4: score configs ─────────────────────────────────────────────────
banner "3/6  Score configs (incl. tool_use_correctness)"
RUN uv run python setup_score_configs.py

# ── Stage 6: human review ──────────────────────────────────────────────────
banner "4/6  Annotation queue (human review)"
RUN uv run python setup_annotation_queues.py

# ── List use cases ─────────────────────────────────────────────────────────
banner "5/6  Registered use cases"
RUN uv run python run_usecase_certification.py --list

# ── Certify the use case ───────────────────────────────────────────────────
banner "6/6  Certify use case: $USE_CASE"
if uv run python run_usecase_certification.py \
      --use-case "$USE_CASE" \
      --dataset "$DATASET" \
      --model "$MODEL" \
      --queue-failures; then
  echo
  echo "✅ Certification run complete. Inspect:"
  echo "   • Langfuse UI → Datasets → $DATASET → Runs → open an item → span tree"
  echo "   • certification_result score comment → per-dimension PASS/FAIL"
  echo "   • Portal (/) → row 'usecase:$USE_CASE' → PASS/FAIL badge"
else
  echo
  echo "❌ Certification run failed for use case '$USE_CASE' — see output above."
fi

banner "Demo complete"
