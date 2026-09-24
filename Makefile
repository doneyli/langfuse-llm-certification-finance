# Deployment Gates for LLMs & AI Agents — one-command workflow.
#
# Quick path for a fresh clone:
#   make install        # install Python deps
#   make preflight      # verify credentials + Langfuse connectivity (go/no-go)
#   make seed           # load sample data + register score configs, queues, prompts
#   make gate           # run the model deployment gate (PASS/FAIL)
#   make ci-gate        # run the agent gate the way CI does (exit 1 on FAIL)
#   make portal         # build + launch the Certification Portal on :8050
#
# Or the whole non-interactive setup at once (stops before the long-running portal):
#   make quickstart
#
# Overridable variables (defaults shown):
#   MODEL=claude-sonnet-4-6   # model for `gate` and `agent-gate`
#   DATASET=certification/financebench-sample   # dataset for `gate`/`export` (see note)
#   USE_CASE=10k-analyst      # agent for `agent-gate` (dataset auto-routes per agent)
#   PY="uv run python"        # pip users: make PY=python ...  (note: `demo` always uses uv)
#
# `agent-gate` auto-selects the golden dataset for the chosen USE_CASE (matching
# scripts/recert_for_prompt.py); pass DATASET=... on the command line to override.
# Credentials are NOT created by any target — they are a human step. See AGENTS.md.

PY      ?= uv run python
PYTEST  ?= uv run pytest
MODEL   ?= claude-sonnet-4-6
DATASET ?= certification/financebench-sample
USE_CASE ?= 10k-analyst
COMPOSE := docker compose -f selfhost/docker-compose.yml

# Per-agent golden dataset (matches recert_for_prompt.py / demo_usecase.sh routing).
# An explicit `DATASET=...` on the command line always wins.
ifeq ($(origin DATASET),command line)
  AGENT_DATASET := $(DATASET)
else ifeq ($(USE_CASE),sentiment-triage)
  AGENT_DATASET := certification/fpb-sample
else ifeq ($(USE_CASE),advisory-draft)
  AGENT_DATASET := certification/advisory-adversarial
else
  AGENT_DATASET := certification/financebench-sample
endif

.DEFAULT_GOAL := help
.PHONY: help install preflight seed gate agent-gate ci-gate demo export portal test up down quickstart

help: ## Show this help
	@echo "Deployment Gates — make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Vars: MODEL=$(MODEL)  DATASET=$(DATASET)  USE_CASE=$(USE_CASE)"

install: ## Install Python dependencies (uv sync; pip fallback if uv absent or PY=python)
	@if command -v uv >/dev/null 2>&1 && [ "$(PY)" != "python" ]; then \
	  uv sync; \
	else \
	  echo "uv not used -> pip install -r requirements.txt (run 'pip install pytest' for the test suite)"; \
	  pip install -r requirements.txt; \
	fi

preflight: ## Verify env, credentials, and Langfuse connectivity (go/no-go)
	$(PY) scripts/preflight.py

seed: preflight ## Load golden datasets (idempotent) + register score configs, queues, prompts
	@for pair in financebench:certification/financebench-sample \
	             fpb:certification/fpb-sample \
	             advisory-adversarial:certification/advisory-adversarial; do \
	  arg=$${pair%%:*}; name=$${pair##*:}; \
	  n=$$($(PY) scripts/dataset_count.py "$$name" 2>/dev/null | tail -1); \
	  if [ "$${n:-0}" -gt 0 ] 2>/dev/null; then \
	    echo "  [skip] $$name already has $$n items (additive loader; not reloading)"; \
	  else \
	    $(PY) setup_datasets.py --dataset "$$arg" --sample; \
	  fi; \
	done
	$(PY) setup_score_configs.py
	$(PY) setup_annotation_queues.py
	$(PY) setup_prompts.py
	@echo "Seed complete. Next: make gate  (or  make agent-gate USE_CASE=...)"

gate: ## Run the model deployment gate (MODEL, DATASET overridable)
	$(PY) run_certification.py --dataset $(DATASET) --model $(MODEL) --queue-failures

agent-gate: ## Run the agent gate (USE_CASE + MODEL overridable; dataset auto-routes per agent)
	@echo "agent-gate: use_case=$(USE_CASE)  model=$(MODEL)  dataset=$(AGENT_DATASET)"
	$(PY) run_usecase_certification.py --use-case $(USE_CASE) --dataset $(AGENT_DATASET) --model $(MODEL) --queue-failures

ci-gate: ## Run the agent gate exactly as CI runs it (--ci: exit 1 on FAIL, no queueing)
	@echo "ci-gate: use_case=$(USE_CASE)  model=$(MODEL)  dataset=$(AGENT_DATASET)"
	$(PY) run_usecase_certification.py --use-case $(USE_CASE) --dataset $(AGENT_DATASET) --model $(MODEL) --ci

demo: ## Full-lifecycle demo for one agent (scripts/demo_usecase.sh; uses uv)
	bash scripts/demo_usecase.sh

export: ## Export the evidence pack for the latest run (markdown)
	$(PY) export_results.py --dataset $(DATASET)

portal/frontend/dist/index.html: portal/frontend/package.json
	cd portal/frontend && npm install && npm run build

portal: portal/frontend/dist/index.html ## Build (once) + launch the Certification Portal on :8050
	$(PY) -m portal.app

test: ## Run the offline test suite (no credentials needed)
	$(PYTEST) --ignore=tests/test_certification.py -q

up: ## Start the self-hosted Langfuse stack (Docker Compose)
	$(COMPOSE) up -d
	@echo "Langfuse starting at http://localhost:3000 — create an Org+Project and API keys, then fill .env"

down: ## Stop the self-hosted Langfuse stack
	$(COMPOSE) down

quickstart: install preflight seed gate ## install -> preflight -> seed -> one gate run (stops before portal)
	@echo ""
	@echo "Setup done. Launch the dashboard with:  make portal   (then open http://localhost:8050)"
