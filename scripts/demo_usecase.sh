#!/usr/bin/env bash
#
# AI agent deployment gate — end-to-end demo / runbook.
#
# Walks the full eval lifecycle: datasets -> prompts -> score configs ->
# annotation queues -> deployment-gate run on one agent -> where to inspect results.
# The narration that goes with it is docs/usecase-runbook.md §6.
#
# All three agents (10k-analyst, sentiment-triage, advisory-draft) are
# implemented and registered; pick one via USE_CASE below.
#
# Usage:
#   bash scripts/demo_usecase.sh                 # default: 10k-analyst on financebench-sample
#   USE_CASE=sentiment-triage bash scripts/demo_usecase.sh     # picks fpb-sample by default
#   USE_CASE=advisory-draft bash scripts/demo_usecase.sh       # picks advisory-adversarial
#   MODEL=claude-haiku-4-5-20251001 bash scripts/demo_usecase.sh   # whole-system lift: cheap model still passes
#   USE_CASE=advisory-draft ADVISORY_TEMPT_NONCOMPLIANT=1 bash scripts/demo_usecase.sh  # the compliance FAIL
#
# Env: LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL, ANTHROPIC_API_KEY
set -euo pipefail

USE_CASE="${USE_CASE:-10k-analyst}"
MODEL="${MODEL:-claude-sonnet-4-6}"

# Each agent is gated against its own golden dataset (matching the
# recert_for_prompt.py routing); override with DATASET=... if needed.
case "$USE_CASE" in
  sentiment-triage) DEFAULT_DATASET="certification/fpb-sample" ;;
  advisory-draft)   DEFAULT_DATASET="certification/advisory-adversarial" ;;
  *)                DEFAULT_DATASET="certification/financebench-sample" ;;
esac
DATASET="${DATASET:-$DEFAULT_DATASET}"

RUN() { echo; echo "▶ $*"; "$@"; }
banner() { echo; echo "════════════════════════════════════════════════════════════"; echo "  $*"; echo "════════════════════════════════════════════════════════════"; }

cd "$(dirname "$0")/.."

banner "AI Agent Deployment Gate Demo  (agent=$USE_CASE  model=$MODEL)"
echo "Dataset: $DATASET"

# ── Stage 1: golden data ───────────────────────────────────────────────────
# setup_datasets.py upserts by stable item id, but datasets seeded before stable
# ids carry random ids and would gain one more copy. Load only if empty.
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
    echo "  [skip] $2 already has $n items (not reloading)"
  else
    RUN uv run python setup_datasets.py --dataset "$1" --sample
  fi
}
load_if_empty financebench certification/financebench-sample
load_if_empty fpb certification/fpb-sample
if [ "$USE_CASE" = "advisory-draft" ]; then
  load_if_empty advisory-adversarial certification/advisory-adversarial
fi

# ── Stage 2: prompt management ─────────────────────────────────────────────
banner "2/6  Prompt management (model-gate + agent templates)"
RUN uv run python setup_prompts.py

# ── Stage 4: score configs ─────────────────────────────────────────────────
banner "3/6  Score configs (pass/fail scores are BOOLEAN)"
RUN uv run python setup_score_configs.py

# ── Stage 6: human review ──────────────────────────────────────────────────
banner "4/6  Annotation queue (human review)"
RUN uv run python setup_annotation_queues.py

# ── List agents ────────────────────────────────────────────────────────────
banner "5/6  Registered agents"
RUN uv run python run_usecase_certification.py --list

# ── Run the deployment gate ────────────────────────────────────────────────
banner "6/6  Deployment gate: $USE_CASE"
if uv run python run_usecase_certification.py \
      --use-case "$USE_CASE" \
      --dataset "$DATASET" \
      --model "$MODEL" \
      --queue-failures; then
  echo
  echo "✅ Gate run complete (the verdict is printed above). Inspect:"
  echo "   • Langfuse → Experiments → the run (URL printed above) → open an item"
  echo "     → trace tree + Agent Graph tab"
  echo "   • certification_result (true/false) comment → per-dimension PASS/FAIL"
  echo "   • Prompts → the agent's prompt → Metrics → scores per prompt version"
  echo "   • Portal (make portal) → row 'usecase:$USE_CASE' → PASS/FAIL badge"
else
  echo
  echo "❌ The gate run for '$USE_CASE' errored — see output above."
fi

banner "Demo complete"
