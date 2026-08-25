# Deployment Gates for LLMs & AI Agents in Financial Services

A **deployment gate** for AI agents and LLM models in financial services: run the
system against golden financial datasets, score every output with domain-specific
evaluators, and produce a **PASS/FAIL verdict backed by reviewable evidence** — at
deployment velocity. Built on [Langfuse](https://langfuse.com) experiments and
open-source financial evaluation datasets, with [ClickHouse](https://clickhouse.com)
as the analytics backend.

📖 **Companion blog post:** [Building Deployment Gates for LLMs and AI Agents in Financial Services](https://langfuse.com/blog/2026-07-15-llm-certification-financial-services) — the story and reasoning behind this repo.

🤖 **Coding agents:** start with [`AGENTS.md`](AGENTS.md) — the golden path, credential checkpoints, preconditions, and success checks in one place (`make quickstart`). It also covers the [Langfuse CLI](https://langfuse.com/docs/api-and-data-platform/features/cli) and [agent skill](https://langfuse.com/docs/api-and-data-platform/features/agent-skill) for driving your Langfuse deployment.

> **A note on the word "certification."** It's the internal name we used for this
> process while building it, and it survives in the repository name, the dataset
> names, the script names, and the screenshots below. Nothing here issues a
> certificate. The pipeline is a deployment gate that produces reviewable evidence —
> regulatory approval, independent validation, legal review, and accountable human
> sign-off stay with people.

Two gate modes ship out of the box:

| Mode | Entry point | What clears the gate | Ships with |
|---|---|---|---|
| **Model gate** | `run_certification.py` | one LLM call per item; primary accuracy score ≥ threshold | any OpenAI-compatible endpoint or Claude model |
| **[AI agent gate](#gating-the-whole-agent)** | `run_usecase_certification.py` | a multi-step **agent** per item; **every** gate dimension must pass (accuracy, groundedness, compliance, tool use…) | three registered agents: **`10k-analyst`** (SEC-filing QA with a calculator tool), **`sentiment-triage`** (classification with human routing), **`advisory-draft`** (compliance-gated client prose) |

The pieces form Langfuse's [AI Engineering Loop](docs/ai-engineering-loop.md)
(Trace → Monitor → Build Datasets → Experiment → Evaluate) end to end: production
failures are promoted back into golden datasets, and CI re-runs the affected gate
when a prompt is promoted in Langfuse (one-time setup:
[loop activation checklist](docs/loop-activation-checklist.md)). See
[Keeping the evidence current](#keeping-the-evidence-current).

## The setup

Model risk management teams don't approve raw models — they approve the **AI agents
and applications** built on them: a 10-K analysis assistant, a sentiment triage
pipeline, a client-advisory drafting tool. The hard part isn't running the test
once. It's that LLM systems change much faster than the models underneath them,
because prompts get promoted, models get swapped, and tools and retrieval logic
evolve. **By the time a manual approval lands, the system it tested has often
already changed.**

The cost of a miss is asymmetric. When a gate catches a bad number during testing —
say a FinanceBench run comes back at 81.3% against an 85% accuracy bar — it costs
nothing: a number turns red on a dashboard and someone investigates before the
release. Caught after deployment, the same miss means a client received a wrong
number in a 10-K summary, and in financial services that is a reportable incident.

So instead of scaling the manual review, **we scripted the evidence.** The pipeline
runs an LLM system — a single model call, or a whole multi-step agent — against
golden financial datasets, scores every output with domain-specific evaluators, and
produces a PASS/FAIL verdict with a full, reproducible audit trail. What took two
weeks of manual testing becomes a one-day, one-command run — and, once wired into
CI, a check that re-runs itself whenever the system changes.

## Architecture

The pipeline implements Langfuse's AI Engineering Loop — **Trace → Monitor → Build
Datasets → Experiment → Evaluate** — as a cycle, with Langfuse as the system of
record for datasets, traces, scores, prompts, and review queues:

```
 BUILD DATASETS          EXPERIMENT                    EVALUATE
+------------------+    +------------------------+    +------------------------+    +--------------+
| Golden Datasets  |    | Experiment Runners     |    | Evaluators             |    | Results      |
| - FinanceBench   |--->| run_certification.py   |--->| Deterministic:         |--->| Scores/item  |
| - Financial PB   |    |   model gate (any      |    |   accuracy, sentiment, |    | PASS / FAIL  |
| - advisory-      |    |   OpenAI-compat model) |    |   compliance, complete-|    | Audit trail  |
|   adversarial    |    | run_usecase_certifi-   |    |   ness, tool trajectory|    | Portal (SPA) |
| - promoted prod  |    |   cation.py (3 Claude  |    | LLM-as-a-Judge:        |    | Export MD/   |
|   traces         |    |   agents -> nested     |    |   groundedness         |    |   JSON/CSV   |
+------------------+    |   traces)              |    | Gates: threshold +     |    +--------------+
     ^                  +------------------------+    |   multi-dimensional    |
     |                    ^                           +------------------------+
     |                    | Edge B -- ship -> re-gate: promoting a prompt to
     |                    | `production` fires repository_dispatch ->
     |                    | prompt-recert.yml -> scripts/recert_for_prompt.py --ci
     |                  +------------------------+
     |                  | Langfuse Prompt Mgmt   |   managed templates: financial-qa,
     |                  | (edit / version /      |   financial-sentiment, usecase-*
     |                  |  promote)              |   (fetched by the runners at run time)
     |                  +------------------------+
     |
     | Edge A -- observe -> develop: promote_trace_to_dataset.py --from-queue
     | (flagged trace -> dataset item `prod-<traceId>`; a human supplies the answer)
     |
+------------------+    +------------------------+    +------------------------+
| "Certification   |<---| monitor_production.py  |<---| Production traces      |
|  Review" queue   |    | compliance + complete- |    | (your deployed app,    |
| (human review)   |    | ness on live traces;   |    |  instrumented with     |
+------------------+    | --queue-violations     |    |  Langfuse)             |
 MONITOR                +------------------------+     TRACE
```

The top row is the deployment-gate path; the bottom row is production observability.
The two feedback edges close the loop: **Edge A** turns reviewed production failures
into golden dataset items (the next run regression-tests them), and **Edge B**
re-runs the gate automatically whenever a managed prompt is promoted in Langfuse.

## Golden datasets

The gate is only as trustworthy as the ground truth it runs against. Golden datasets
are the reproducible test sets every run scores against — two open financial
benchmarks plus a small adversarial set for the compliance gate.

### `setup_datasets.py` — Dataset loader

Loads golden financial datasets into Langfuse from HuggingFace or embedded sample files.

```
Options:
  --dataset {financebench,fpb,advisory-adversarial,all}
                                     Which dataset(s) to load (default: all —
                                     covers financebench + fpb; advisory-adversarial
                                     is opt-in only)
  --sample                           Use embedded sample data (offline mode)
  --prefix PREFIX                    Dataset name prefix (default: certification)
  --dry-run                          Preview without creating
```

**Supported datasets:**

| Dataset | Items | Source | Focus |
|---------|-------|--------|-------|
| `financebench` | 10 (sample) / 150 (full) | [PatronusAI/financebench](https://huggingface.co/datasets/PatronusAI/financebench) | Financial QA from SEC filings |
| `fpb` | 10 (sample) / ~4850 (full) | [ChanceFocus/en-fpb](https://huggingface.co/datasets/ChanceFocus/en-fpb) | Financial sentiment classification |
| `advisory-adversarial` | 10 (embedded) | `sample_data/advisory_adversarial.json` | Client-update briefs: 3 compliant controls + 7 briefs that each tempt a distinct kind of non-compliant advisory language (guarantees, buy/sell calls, no-risk reassurance, certainty claims, non-public information, concentration advice) |

`advisory-adversarial` loads as `certification/advisory-adversarial` (no `-sample`/`-v1` suffix)
and is deliberately excluded from `--dataset all`. It exists to demo the `advisory-draft` agent's
hard compliance gate: run it with `ADVISORY_TEMPT_NONCOMPLIANT=1` and the agent is nudged toward
prohibited phrasing, so the compliance dimension fails the whole run even when every
other score is perfect.

> **Caveat:** `setup_datasets.py` creates dataset *items* without stable ids, so re-running it
> against an already-loaded dataset duplicates the items (the dataset itself is created
> idempotently). `promote_trace_to_dataset.py`, by contrast, upserts items by id and is safe to
> re-run.

## Running the experiment

Each run is a Langfuse experiment: the system under test executes against every item
in a golden dataset, every output is scored, and the whole thing is traced so the
result is reproducible and reviewable later.

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Node.js 20+ / npm 10+ (to build the portal frontend)
- A Langfuse instance — see [Choose your Langfuse deployment](#choose-your-langfuse-deployment) below
- An LLM API key (OpenAI, Anthropic, or any OpenAI-compatible endpoint)
- Docker 24+ with Compose v2 — only if you self-host Langfuse

### Choose your Langfuse deployment

The pipeline talks to Langfuse purely through `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, and `LANGFUSE_BASE_URL`, so the same code runs against
either deployment. Pick one before running Quick Start.

| | Langfuse Cloud (recommended) | Self-hosted (OSS) |
|---|---|---|
| Best for | Fastest evaluation, no infra to manage | Air-gapped environments, strict data-residency requirements |
| Setup time | ~2 min (signup) | ~10 min (Docker Compose) |
| Cost | Free tier with paid plans above (see [pricing](https://langfuse.com/pricing)) | Infrastructure only — Langfuse OSS is Apache 2.0 |
| Data residency | Langfuse-managed (EU or US region) | Your infrastructure |
| Maintenance | None | You operate Postgres, ClickHouse, Redis, MinIO |
| Prerequisite for this repo | A Cloud account | Docker 24+ with Compose v2 |

#### Option A — Langfuse Cloud

1. Sign up at <https://cloud.langfuse.com/auth/sign-up> (EU) or
   <https://us.cloud.langfuse.com/auth/sign-up> (US).
2. Create an **Organization → Project**.
3. **Settings → API Keys → Create new API keys**. Copy the public + secret keys.
4. Paste them into `.env` (created in [Quick Start step 1](#1-setup)). The
   `LANGFUSE_BASE_URL` already defaults to `https://cloud.langfuse.com`;
   uncomment the US-region line in `.env.example` if you signed up there.

#### Option B — Self-host Langfuse

The repo ships a curated Docker Compose stack with Langfuse pinned to v3. From
the repo root:

```bash
docker compose -f selfhost/docker-compose.yml up -d
# Open http://localhost:3000 → sign up → create Organization + Project →
# Settings → API Keys → Create new API keys
```

Then in [Quick Start step 1](#1-setup), uncomment the self-hosted line in
`.env.example` (and comment the Cloud default) so `.env` ends up with:

```bash
LANGFUSE_BASE_URL=http://localhost:3000
```

See [`selfhost/README.md`](selfhost/README.md) for stop/reset commands,
network-exposure guidance, credential rotation, and pointers to production
self-hosting via Kubernetes. If you plan to run the full 150-item FinanceBench
dataset against a local stack, also read
[Troubleshooting: hangs on long runs](#troubleshooting-hangs-on-long-runs) —
local Langfuse can saturate the OTel queue on long, CoT-heavy runs.

> **Recommendation:** start with Cloud's free tier to get the pipeline running
> end-to-end, then move to self-hosted if compliance or data-residency
> requirements demand it. The scripts, evaluators, dataset
> loaders, portal, and CI workflows are identical for both deployments.

#### Keeping local and Cloud side by side (optional)

You don't have to choose once: keep one credential profile per deployment and
swap. Every script (and the portal) loads `.env` with `override=True`, so the
`.env` *file* always wins over exported shell variables — the swap lever is
which profile the file points to, and `scripts/use_env.sh` manages that via a
symlink:

```bash
mv .env .env.local                      # one-time: keep your current .env as the 'local' profile
cp .env.local .env.cloud                # then edit .env.cloud with your Cloud keys + base URL

bash scripts/use_env.sh status          # which profile is active
bash scripts/use_env.sh cloud           # point .env at .env.cloud
bash scripts/use_env.sh local           # point .env at .env.local

# Run one command against BOTH deployments (sequentially, then restore):
bash scripts/use_env.sh both -- uv run python run_certification.py \
  --dataset certification/financebench-sample --model claude-sonnet-4-6
```

Both profiles are covered by `.gitignore` (`.env.*`). There is no dual-write
mode — a gate run talks to exactly one Langfuse instance — so "send
to both" means running the same command once per profile, which is what
`both --` automates (each instance gets its own traces, scores, and runs).
The portal reads `.env` once at startup: restart it after a switch.

### Quick Start

#### 1. Setup

```bash
git clone https://github.com/doneyli/langfuse-llm-certification-finance.git
cd langfuse-llm-certification-finance
cp .env.example .env    # Edit with your Langfuse + LLM API credentials
```

> The `.env.example` lists three `LANGFUSE_BASE_URL` options (Cloud EU, Cloud
> US, self-hosted) — uncomment exactly one and comment the others before
> running anything. See
> [Choose your Langfuse deployment](#choose-your-langfuse-deployment).

**Install dependencies** (choose one):

```bash
# Recommended: using uv (https://docs.astral.sh/uv/)
uv sync

# Alternative: using pip
pip install -r requirements.txt
```

> The examples below use `uv run` which auto-creates a virtualenv and installs
> dependencies. If you installed with pip, drop the `uv run` prefix and use
> `python` directly.

#### 2. Load sample dataset (offline, no HuggingFace needed)

```bash
uv run python setup_datasets.py --dataset financebench --sample
```

#### 3. Set up Langfuse configuration

```bash
uv run python setup_score_configs.py        # Register score types in Langfuse
uv run python setup_annotation_queues.py    # Create human review queue
uv run python setup_prompts.py              # Register prompt templates
```

#### 4. Run the model gate

```bash
uv run python run_certification.py --dataset certification/financebench-sample \
  --model claude-sonnet-4-6 --queue-failures
```

#### 5. Gate an AI agent

Gate a whole agent, not just a model. The 10-K Filing Analyst plans, retrieves
evidence, calls a calculator tool, and composes a grounded answer — and passes
only if accuracy **and** groundedness **and** compliance **and** tool use all
clear their bars (see [Gating the whole agent](#gating-the-whole-agent)):

```bash
uv run python run_usecase_certification.py --list    # 10k-analyst, sentiment-triage, advisory-draft
uv run python run_usecase_certification.py --use-case 10k-analyst \
  --dataset certification/financebench-sample --queue-failures
```

#### 6. Build the portal frontend

```bash
cd portal/frontend && npm install && npm run build && cd ../..
```

#### 7. Launch the portal

```bash
uv run python -m portal.app    # Opens on http://localhost:8050
```

#### 8. View results

Open `http://localhost:8050` for the Certification Dashboard — the model and the
agent appear as separate rows (`claude-sonnet-4-6` and `usecase:10k-analyst`) —
or Langfuse UI > **Datasets** > `certification/financebench-sample` > **Runs**.

#### 9. Review failed items

Open your Langfuse UI > **Annotation Queues** > `Certification Review` to review items that failed automated evaluation.

#### 10. Export the evidence

```bash
uv run python export_results.py --dataset certification/financebench-sample
uv run python export_results.py --dataset certification/financebench-sample --format json --output report.json
```

### Full dataset mode

To load all 150 FinanceBench items from HuggingFace (requires internet):

```bash
uv run python setup_datasets.py --dataset financebench        # Downloads from HuggingFace
uv run python run_certification.py --dataset certification/financebench-v1 --model gpt-4o
```

### `run_certification.py` — Experiment runner

Runs a Langfuse dataset through a model, evaluates outputs, and reports pass/fail.

```
Options:
  --dataset DATASET             Langfuse dataset name (required)
  --model MODEL                 Model to run through the gate (default: claude-sonnet-4-6)
  --endpoint URL                LLM API base URL (for custom gateways)
  --threshold FLOAT             Pass threshold (default: 0.85)
  --max-concurrency N           Concurrent API calls (default: 5)
  --evaluators {all,...}        Which evaluators to run
  --run-name NAME               Custom experiment run name (default: auto-generated)
  --queue-failures              Route failed items to annotation queue for human review
  --ci                          Exit 1 unless the gate passes (for CI)
  --dry-run                     Preview dataset items only
  --system-prompt-file PATH     Markdown file used verbatim as the LLM system message
                                (e.g. prompts/finance_expert.md — see "Domain-adapted
                                variants" below)
  --label NAME                  Variant slug appended to model name in metadata + run
                                name (e.g. finance-expert). Each label becomes a
                                distinct row on the portal dashboard.
```

#### Troubleshooting: hangs on long runs

If a run silently freezes near the tail (e.g. progress to ~item 140 of 150, then stops emitting log lines for 10+ minutes with the Python process alive but consuming 0% CPU), the cause is OTel `BatchSpanProcessor` queue saturation against a slow local Langfuse — large spans (long evidence + long CoT outputs) accumulate faster than the local instance can ingest them, the export queue fills, and the pipeline deadlocks.

`run_certification.py` now sets safer OTel defaults at startup (queue 20k, batch 64, flush every 2s, export timeout 120s). To override, set the env vars before running:

```bash
OTEL_BSP_MAX_QUEUE_SIZE=20000      # default queue is 2048 — too small for 150+ items
OTEL_BSP_MAX_EXPORT_BATCH_SIZE=64  # smaller batches export faster, less queue pressure
OTEL_BSP_SCHEDULE_DELAY=2000       # flush every 2s instead of every 5s
OTEL_BSP_EXPORT_TIMEOUT=120000     # give a slow local Langfuse 2 minutes per export
LANGFUSE_FLUSH_AT=64
LANGFUSE_FLUSH_INTERVAL=2
```

The hang does not occur against Langfuse Cloud (faster ingestion). Only seen against a local self-hosted Langfuse with the full 150-item FinanceBench dataset and a CoT-heavy system prompt.

## Evaluators

Evaluators are where domain knowledge lives. The pipeline uses **both** deterministic
and LLM-as-a-Judge evaluators — deterministic checks handle objective, verifiable
facts (number matching, prohibited phrases), while the LLM judge assesses subjective
quality dimensions (groundedness, faithfulness to source documents).

`evaluators.py` is an importable module of evaluation functions. All follow the Langfuse SDK signature.

**Deterministic evaluators** (fast, cheap, reproducible):

| Evaluator | Type | What It Checks |
|-----------|------|---------------|
| `numerical_accuracy_evaluator` | Item | Extracts numbers, compares with 5% tolerance |
| `exact_match_evaluator` | Item | Strict string containment |
| `sentiment_evaluator` | Item | Sentiment classification accuracy |
| `regulatory_compliance_evaluator` | Item | Scans for prohibited financial phrases |
| `response_completeness_evaluator` | Item | Response length and structure (emits score `completeness`) |
| `tool_use_correctness_evaluator` | Item | Trajectory check: did the agent invoke the expected tool for the question type (`calculate`, `route`, `compliance-self-check`)? Returns no score for non-agent (plain string) outputs |

**LLM-as-a-Judge evaluators** (nuanced, catches qualitative failures):

| Evaluator | Type | What It Checks |
|-----------|------|---------------|
| `groundedness_evaluator` | Item | Faithfulness + completeness vs source filing evidence |

The groundedness evaluator sends the model's output, source evidence, and question to a judge model (default: `claude-sonnet-4-6`, configurable via `JUDGE_MODEL` env var) with a financial auditor rubric. It scores **faithfulness** (are claims supported by the documents?) and **completeness** (does the answer cover relevant information?), combined into a weighted score (70% faithfulness, 30% completeness). It only runs on items that include source evidence (e.g., FinanceBench).

**Run-level evaluators** (aggregate across all items):

| Evaluator | Type | What It Checks |
|-----------|------|---------------|
| `average_score_evaluator(name)` | Run | Averages a named score across all items (emits `avg_<name>`) |
| `certification_gate(name, threshold)` | Run | PASS/FAIL based on a single score threshold (model gate) |
| `usecase_certification_gate(thresholds)` | Run | Multi-dimensional PASS/FAIL — every dimension in the dict must clear its threshold; a dimension with no scores averages 0.0 and fails (agent gate) |

### Why both types of evaluators

They cover different failure modes. For example, in our Haiku run:
- **Numerical accuracy** (deterministic): 60% — Haiku often gets the numbers wrong
- **Groundedness** (LLM judge): 97% — but when it has evidence, it faithfully uses it

Without the LLM judge, you'd just see "60%, FAILED" and assume the model is unreliable. With it, you can see the failure is specifically in numerical reasoning, not in faithfulness to source material. That distinction matters for model risk assessments.

## The gate and the evidence export

A gate turns a run's scores into the one thing a reviewer needs: a PASS/FAIL verdict,
plus the evidence behind it. The model gate checks whether the primary accuracy
metric meets the configured threshold (default: 85%); the run either PASSES or FAILS.
The agent gate works the same way but with a **multi-dimensional gate** — every
dimension registered for the agent (accuracy, groundedness, compliance, tool-use
correctness) must clear its own threshold at once (see
[Gating the whole agent](#gating-the-whole-agent)).

### Changing pass thresholds

```bash
uv run python run_certification.py --dataset my-dataset --threshold 0.90
```

Or modify `DEFAULT_THRESHOLD` in `evaluators.py`.

### Where results appear in Langfuse

Langfuse is the source of truth for the evidence:

- **Item-level scores** (numerical_accuracy, groundedness, etc.) appear on each trace under the dataset run in **Datasets > [dataset] > Runs**.
- **Run-level scores** (certification_result, avg_numerical_accuracy, avg_groundedness) are persisted as scores on the first experiment trace. You can find them by searching for scores named `certification_result` in the Langfuse Scores view, or by clicking into any trace from the dataset run.

### `export_results.py` — Evidence exporter

Exports experiment scores as a reviewable evidence pack for compliance/AMRM report generation.

```
Options:
  --dataset DATASET          Langfuse dataset name (required)
  --run-name NAME            Specific run (default: latest)
  --format {markdown,json,csv}  Output format (default: markdown)
  --output FILE              Output file (default: stdout)
```

## Gating the whole agent

Beyond gating *models*, the pipeline can gate **AI agents** — multi-step
systems deployed for a specific business purpose. An agent clears the gate only
if the **whole system** passes every production-readiness bar at
once — a **multi-dimensional gate** where every dimension must pass, not just a
single accuracy score.

| | Model gate | AI agent gate |
|---|---|---|
| Entry point | `run_certification.py` | `run_usecase_certification.py` |
| `task` | one LLM call | a multi-span **agent** |
| Trace | flat | nested (one span per agent step) |
| Gate | one score ≥ threshold | **multi-dimensional**, all must pass |
| Dashboard row | model name | `usecase:<name>` |
| Models | any OpenAI-compatible endpoint or Claude | Claude only (agents call the native Anthropic SDK) |

**The three registered agents** — each defines its own trace shape and gate.
Which dimensions an agent gates on is a property of the agent; the thresholds
themselves live in [`cicd/thresholds.json`](cicd/thresholds.json), never in a CLI
flag:

| Agent | Steps (spans) | Gate — all must pass |
|----------|--------------------|----------------------|
| `10k-analyst` — grounded SEC-filing QA with a calculator tool | plan → retrieve-evidence → calculate (tool) → compose-answer | numerical_accuracy ≥ 0.85, groundedness ≥ 0.80, regulatory_compliance = 1.00, tool_use_correctness ≥ 0.90 |
| `sentiment-triage` — classify sentiment, route low-confidence items to a human | classify → rationale → route (tool) | sentiment_accuracy ≥ 0.85, regulatory_compliance = 1.00, tool_use_correctness = 1.00 |
| `advisory-draft` — grounded client summary with a hard compliance gate | analyze → draft → compliance-self-check (tool) | groundedness ≥ 0.80, regulatory_compliance = 1.00, completeness ≥ 0.70, tool_use_correctness = 1.00 |

Note how the gates differ per agent: `sentiment-triage` has no groundedness
dimension (no source documents to be faithful to), and `advisory-draft` has no
accuracy dimension at all — it gates on grounded, complete, *compliant* prose.
`regulatory_compliance = 1.00` is a hard gate everywhere: one prohibited phrase
fails the entire run.

```bash
uv run python run_usecase_certification.py --list           # list registered agents
uv run python run_usecase_certification.py --use-case 10k-analyst \
    --dataset certification/financebench-sample --model claude-sonnet-4-6
bash scripts/demo_usecase.sh                                 # full-lifecycle demo
```

```
Options:
  --list                        List registered agents and exit
  --use-case {10k-analyst,sentiment-triage,advisory-draft}
                                Which agent to gate (required unless --list)
  --dataset DATASET             Langfuse dataset name (required unless --list)
  --model MODEL                 Base Claude model for the agent (default: claude-sonnet-4-6)
  --max-concurrency N           Concurrent items (default: 5)
  --run-name NAME               Custom experiment run name (default: auto-generated)
  --queue-failures              Route failed items to the annotation queue
  --ci                          Exit 1 unless every gate dimension passes (for CI)
  --dry-run                     Preview dataset items only
```

`run_usecase_certification.py` runs a Langfuse dataset through a registered
multi-step **agent** (nested trace, one span per step) and applies that agent's
**multi-dimensional gate** — every dimension must pass.

The shared foundation (multi-dimensional gate, trajectory evaluator, agent
registry, tracing helpers) plus all three agents are on `main`. See:

- [`docs/ai-engineering-loop.md`](docs/ai-engineering-loop.md) — **the objective**: how this project forms Langfuse's [AI Engineering Loop](https://langfuse.com/academy/ai-engineering-loop) (Trace → Monitor → Build Datasets → Experiment → Evaluate) end to end, and the CI/CD-for-prompts story
- [`docs/loop-activation-checklist.md`](docs/loop-activation-checklist.md) — one-time console/config to turn the feedback edges on (Langfuse automations, GitHub secrets/PAT)
- [`docs/usecase-certification.md`](docs/usecase-certification.md) — implementation spec (3 agents)
- [`docs/usecase-architecture.md`](docs/usecase-architecture.md) — architecture + eval-lifecycle component map
- [`docs/usecase-runbook.md`](docs/usecase-runbook.md) — runbook + demo narration
- [`docs/hosting-demo.md`](docs/hosting-demo.md) — plan for hosting a public read-only demo (Vercel + Langfuse Cloud; designed, not yet executed)
- [`cicd/README.md`](cicd/README.md) — **the quality bar as code**: every gate threshold in one reviewable file, the hard-vs-loose split, what no bar covers, and what this repo deliberately does *not* run in CI

## A portal for the people who sign off

A gate is only useful if the people accountable for the release can read its
evidence. The **Certification Portal** is a web dashboard for business and
compliance stakeholders to view gate status at a glance.

The UI is a React SPA built with [Click UI](https://clickhouse.design/click-ui), the official ClickHouse design system. FastAPI exposes the JSON API and serves the built SPA. It ships with a light/dark theme toggle and a per-page provenance strip that deep-links every number back to its source in Langfuse ("source of truth").

The dashboard is a **latest-run-per-(dataset, model) matrix**: for each dataset it shows the newest run of every `metadata.model` value — plain models, `--label` variants (e.g. `claude-opus-4-7-finance-expert`), and agents (`usecase:10k-analyst`) each get their own row. Each row's *primary score* is picked per run — `avg_numerical_accuracy` if present, else `avg_sentiment_accuracy`, `avg_groundedness`, `avg_exact_match`, `avg_completeness`, or the first other `avg_*` score — and labeled with the metric name.

### Running the portal

```bash
# First time: build the frontend
cd portal/frontend && npm install && npm run build && cd ../..

uv run python -m portal.app                     # Default: http://localhost:8050
PORTAL_PORT=9000 uv run python -m portal.app    # Custom port
```

### Frontend development (live reload)

```bash
# Terminal 1: API
uv run python -m portal.app

# Terminal 2: Vite dev server — proxies /api to :8050
cd portal/frontend && npm run dev        # http://localhost:5173
```

### Pages

| Page | URL | Description |
|------|-----|-------------|
| Dashboard | `/` | Gate matrix — which models pass/fail against which datasets |
| Breakdown | `/breakdown/{dataset}/{run}` | Evaluator scores (bar chart + table) for a specific run |
| History | `/history/{dataset}` | Timeline of all runs with trend chart |
| Run Detail | `/run/{dataset}/{run}` | Per-item scores with links to Langfuse traces |

### JSON API

All pages have corresponding JSON endpoints under `/api/`:

| Endpoint | Returns |
|----------|---------|
| `GET /api/dashboard` | Gate matrix (all datasets × models) |
| `GET /api/datasets` | Datasets the portal discovered in Langfuse |
| `GET /api/breakdown/{dataset}/{run}` | Evaluator scores for one run |
| `GET /api/history/{dataset}` | All runs for a dataset (trend data) |
| `GET /api/run/{dataset}/{run}` | Per-item scores with trace ids |
| `GET /api/config` | Portal config (Langfuse base URL, used for deep links) |
| `GET /docs` | Interactive Swagger UI for the API |

```bash
curl http://localhost:8050/api/dashboard | python -m json.tool
curl http://localhost:8050/api/history/certification/financebench-sample
```

The portal reads live data from your Langfuse instance (same `LANGFUSE_*` credentials) with a 60-second TTL cache. Datasets are **discovered automatically** from Langfuse (paginated `/api/public/v2/datasets`), filtered to names starting with `certification/` — override the prefix with `PORTAL_DATASET_PREFIX`, or pin an explicit list with a comma-separated `PORTAL_DATASETS`.

### Portal vs Langfuse dashboards

Some metrics can also be visualized using [Langfuse Custom Dashboards](https://langfuse.com/docs/metrics/features/custom-dashboards) (created in the UI — no API for dashboard creation). Use both:

| What to track | Where | Why |
|---|---|---|
| Score trends over time | **Langfuse dashboard** | Native widget: `scores-numeric` view, dimension `name`, time granularity `day` |
| Compliance violations | **Langfuse dashboard** | Native widget: filter `name=regulatory_compliance`, count where value=0 |
| Cost & latency by model | **Langfuse dashboard** | Native widget: `observations` view, dimension `providedModelName` |
| Pass/fail gate matrix | **Portal** | Langfuse can't join dataset run metadata to scores or show threshold-based badges |
| Run-level aggregation per experiment | **Portal** | Dashboards query individual scores, not scoped to a specific dataset run |
| Per-item drill-down with all scores | **Portal** | Dashboards show aggregate charts, not item-level tables |
| Run history & trend by dataset | **Portal** | No dataset run concept in the dashboard query engine |

**Recommended Langfuse dashboard setup** (create manually in UI > Dashboards > New):
1. **Avg scores over time** — line chart, `scores-numeric` view, measure `avg(value)`, dimension `name`, time granularity `day`
2. **Compliance violations** — bar chart, `scores-numeric` view, filter `name=regulatory_compliance`, filter `value=0`, measure `count`
3. **Score distribution** — bar chart, `scores-numeric` view, measure `avg(value)`, dimension `name`
4. **Cost by model** — bar chart, `observations` view, measure `sum(totalCost)`, dimension `providedModelName`

## Interacting with Langfuse: CLI & agent skill

Beyond the portal and the Python scripts, Langfuse ships two tools that make it
much easier — especially for **coding agents** — to inspect and drive a live
Langfuse deployment. Both authenticate with the **same `.env` credentials** this
repo already uses (no extra login), and neither is required to run the gate.

- **[Langfuse CLI](https://langfuse.com/docs/api-and-data-platform/features/cli)** — a thin wrapper over the full REST API. Quick checks after a run:
  ```bash
  npx langfuse-cli --env .env api datasets list          # confirm golden datasets loaded
  npx langfuse-cli --env .env api traces list --limit 10 # recent gate-run traces
  npx langfuse-cli --env .env api prompts list           # managed prompts
  ```
  (Install once with `npm i -g langfuse-cli`, then call it as `langfuse api …`.)
- **[Langfuse agent skill](https://langfuse.com/docs/api-and-data-platform/features/agent-skill)** — an Agent-Skills-standard skill (Claude Code, Cursor, Windsurf…) that teaches an agent Langfuse best practices and drives the CLI: query traces, build datasets, migrate prompts. Add it with `npx skills add langfuse/skills --skill "langfuse"`.

See [`AGENTS.md`](AGENTS.md) for how these fit the end-to-end agentic workflow.

## Keeping the evidence current

A gate that runs once is stale the moment the system changes. Keeping the evidence
current is what turns a one-off test into Langfuse's
[AI Engineering Loop](docs/ai-engineering-loop.md) (Trace → Monitor → Build Datasets
→ Experiment → Evaluate): production failures flow back into golden datasets, and
prompt promotions re-run the gate automatically.

### Prompt management

Prompts are managed in Langfuse rather than hardcoded. This enables versioning, A/B testing, and prompt updates without code changes.

```bash
uv run python setup_prompts.py    # Creates all 6 managed prompts (model gate + agent steps)
```

**Managed prompts:**

| Prompt | Variables | Used For |
|--------|-----------|----------|
| `financial-qa` | `{{evidence}}`, `{{question}}` | FinanceBench items with filing excerpts (model gate) |
| `financial-sentiment` | `{{text}}` | Financial PhraseBank sentiment classification (model gate) |
| `usecase-10k-analyst-compose` | `{{question}}`, `{{operands}}`, `{{computed}}`, `{{citations}}` | 10-K Analyst agent — compose step |
| `usecase-sentiment-classify` | `{{text}}` | Sentiment Triage agent — classify step |
| `usecase-advisory-analyze` | `{{question}}`, `{{evidence}}` | Advisory Drafting agent — analyze step |
| `usecase-advisory-draft` | `{{facts}}` | Advisory Drafting agent — draft step |

Only the free-form agent steps are Langfuse-managed. The 10-K Analyst's *plan* and *extract*
steps emit JSON the agent parses, so their prompts stay code-owned
(`agents/financial_analyst.py`) to keep parsing stable against UI edits.

To update a prompt:

1. Open Langfuse UI > **Prompts** > select a prompt
2. Edit the prompt text — a new immutable version is created automatically
3. Test the new version by running an experiment
4. Move the `production` label to the new version to deploy it
5. To roll back, reassign `production` to a previous version

The experiment runner always fetches the `production`-labeled version. If Langfuse is unavailable, it falls back to hardcoded defaults so the pipeline never breaks. Promoting a prompt to `production` can also [trigger an automatic re-run of the gate in CI](#cicd-integration) — see loop Edge B.

### Human review (annotation queues)

The pipeline supports human-in-the-loop review for compliance sign-off and evaluator calibration — the accountable humans the gate produces evidence *for*.

```bash
uv run python setup_score_configs.py        # Creates human_accuracy and human_groundedness score configs
uv run python setup_annotation_queues.py    # Creates "Certification Review" queue
```

Pass `--queue-failures` to automatically route low-scoring items to the annotation queue:

```bash
uv run python run_certification.py --dataset certification/financebench-sample \
  --model claude-haiku-4-5-20251001 --queue-failures
```

Items are queued when:
- The primary accuracy score is 0 (completely wrong answer)
- The groundedness score is below 0.5 (poorly grounded in source evidence)

**Reviewer workflow:**

1. Open Langfuse UI > **Annotation Queues** > **Certification Review**
2. For each item, the reviewer sees the original question, the model's response, and the source evidence
3. Score `human_accuracy` (Correct / Partially Correct / Incorrect) and `human_groundedness` (Fully Grounded / Partially Grounded / Not Grounded)
4. Click **Complete + next** to proceed

Human annotations serve two purposes:
- **Compliance audit trail** — documented human sign-off on gate results
- **Evaluator calibration** — compare human scores against automated scores to validate the evaluation rubrics

### Production monitoring

Once a model or agent clears the gate and is deployed, `monitor_production.py` continuously monitors live traces for compliance violations and quality degradation.

```
Options:
  --hours N              Look back N hours (default: 1)
  --tags TAG [TAG ...]   Filter traces by tags
  --trace-name NAME      Filter traces by name
  --limit N              Max traces to process (default: 100)
  --queue-violations     Route flagged traces to the human-review queue (feedback edge)
  --dry-run              Preview without posting scores
```

Run on a cron schedule to catch issues in real time:

```bash
# Every 15 minutes, check the last hour of production traces
*/15 * * * * cd /path/to/repo && uv run python monitor_production.py --hours 1 --tags production

# Or filter by your application's trace name
*/15 * * * * cd /path/to/repo && uv run python monitor_production.py --trace-name my-finance-app
```

It fetches recent traces (filtered by time window, tags, or trace name), skips traces that already have compliance scores (idempotent), runs `regulatory_compliance` and `completeness` evaluators on each, posts scores back to Langfuse, and exits with code 1 if compliance violations are detected — enabling integration with alerting systems.

For subjective quality monitoring (groundedness, helpfulness), configure LLM-as-a-Judge evaluators directly in the Langfuse UI (**Evaluators** > **Set up Evaluator**), targeting "Live Observations" with a sampling rate to manage cost. See [Langfuse LLM-as-a-Judge docs](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge).

### `promote_trace_to_dataset.py` — feedback loop (trace → golden dataset)

Closes the observation → development feedback edge of the [AI Engineering Loop](docs/ai-engineering-loop.md): promotes a flagged/reviewed production trace into a golden dataset item so the next run regression-tests it. Human-gated — it captures the trace *input* but leaves the correct answer to a reviewer (or `--expected`), never copying the suspect trace output as ground truth. Idempotent (item id `prod-<traceId>`, linked via `source_trace_id`).

```
Options:
  --dataset NAME         Target dataset (required, e.g. certification/financebench-sample)
  --trace-id ID          Trace to promote (repeatable)
  --from-queue           Promote every trace in the "Certification Review" queue
  --expected TEXT        Correct answer to store (else flagged needs_expected_review)
  --note TEXT            Optional note stored in item metadata
  --dry-run              Preview without creating items
```

### `scripts/recert_for_prompt.py` — feedback loop (prompt promotion → re-run the gate)

Closes the ship → re-gate feedback edge of the [AI Engineering Loop](docs/ai-engineering-loop.md): given the name of a prompt that was just promoted in Langfuse, it re-runs the gate(s) that depend on that prompt with `--ci`, so a regressing prompt promotion fails CI instead of silently shipping. Invoked by the [`prompt-recert.yml` workflow](#cicd-integration) on `repository_dispatch`; also runnable locally.

```
Options:
  --prompt-name NAME     Changed prompt (required) — routed by name to its gate target(s)
  --model MODEL          Optional model override passed through to the re-run
```

**Prompt → gate routing:**

| Promoted prompt | Re-runs the gate for |
|-----------------|--------------|
| `financial-qa` | model gate on `certification/financebench-sample` |
| `financial-sentiment` | model gate on `certification/fpb-sample` |
| `usecase-10k-analyst-compose` | `10k-analyst` on `certification/financebench-sample` |
| `usecase-sentiment-classify` | `sentiment-triage` on `certification/fpb-sample` |
| `usecase-advisory-analyze`, `usecase-advisory-draft` | `advisory-draft` on `certification/advisory-adversarial` |

An unmapped prompt name is a no-op (exit 0); a failed gate exits 1.

### CI/CD integration

Use `--ci` to fail the process on a gate failure (exit code 1):

```bash
uv run python run_certification.py \
  --dataset certification/financebench-sample \
  --model claude-haiku-4-5-20251001 \
  --threshold 0.85 \
  --ci
```

Or run an agent gate exactly the way CI runs it (`--ci`, no failure queueing):

```bash
make ci-gate USE_CASE=advisory-draft
```

#### The quality bar as code

Every gate bar — the four agent gates and the model-cert default threshold —
lives in [`cicd/thresholds.json`](cicd/thresholds.json), not in the agent
modules. Loosening a bar is therefore a reviewable diff in a pull request, and
[`cicd/README.md`](cicd/README.md) explains the hard-vs-loose split, what no bar
covers (persona and tone are not gated), and why a missing entry raises rather
than defaulting — `usecase_certification_gate` is `all()` over the dimensions it
is given, so an empty gate would certify everything as PASSED.
`tests/test_gate_thresholds.py` fails on any drift between the file and the agent
registry, and on a gated dimension that no evaluator produces.

#### Pytest gates

The test suite splits into an **offline suite** (no credentials, runs on every PR) and a **live gate** (real gate runs against Langfuse):

**Offline suite** — pure/monkeypatched unit tests, no Langfuse or API keys needed. This is what actually gates PRs (via the [`tests.yml` workflow](#github-actions)):

```bash
uv run pytest --ignore=tests/test_certification.py -v
```

| Test file | Covers |
|-----------|--------|
| `test_usecase_foundation.py` | Multi-dimensional gate, `tool_use_correctness` trajectory evaluator, agent registry |
| `test_financial_analyst.py` | 10-K Analyst: `safe_eval` calculator safety, lenient JSON parsing, calculator routing |
| `test_sentiment_triage.py` | Sentiment Triage: label/confidence parsing, driver phrases, confidence routing |
| `test_advisory_draft.py` | Advisory Draft: 3-span flow, compliance self-check, hard compliance-FAIL gate |
| `test_promote_trace_to_dataset.py` | Loop Edge A: deterministic `prod-<traceId>` ids, never-copy-suspect-output guard |
| `test_recert_for_prompt.py` | Loop Edge B: prompt→target routing, `--ci` wiring, drift guard vs `setup_prompts.py`, skip-vs-pass job summary |
| `test_gate_thresholds.py` | The quality bar: registry↔`cicd/thresholds.json` drift, every gated dimension has an evaluator, a missing bar raises instead of certifying everything |

**Live gate** — `tests/test_certification.py` runs real experiments and asserts pass/fail. Requires Langfuse credentials, `ANTHROPIC_API_KEY`, and seeded datasets; it runs in the `certification.yml` workflow, not on PRs:

```bash
# Run all live gate tests
uv run pytest tests/test_certification.py -v

# Run only FinanceBench tests
uv run pytest tests/test_certification.py -v -k financebench

# Override model and threshold via env vars
CERT_MODEL=claude-sonnet-4-6 CERT_THRESHOLD=0.90 uv run pytest tests/test_certification.py -v
```

#### GitHub Actions

The pipeline ships three workflows that automate the loop. `tests.yml` runs
out of the box (no secrets); the two live workflows activate
once you add the four secrets listed below to your repository:

**`tests.yml` — offline tests.** Runs the offline suite (`pytest --ignore=tests/test_certification.py`) on every pull request and every push to `main`. No secrets required.

**`certification.yml` — live gate.**

- **Triggers:** manual dispatch (with configurable model/threshold) and push to `main` when evaluators, runners, agents (`agents/**`, `run_usecase_certification.py`, `cert_common.py`), prompts, or dependency config change
- **Jobs:** FinanceBench and FPB model gates plus a `10k-analyst` agent gate (`certification/financebench-sample`, `--ci`) run in parallel, then the live pytest gate runs (`if: always()`, so it reports even when a gate job fails)
- **Secrets required:** `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, `ANTHROPIC_API_KEY`

To trigger manually: **Actions** > **LLM Certification** > **Run workflow** > choose model and threshold.

**`prompt-recert.yml` — re-run the gate on prompt promotion (loop Edge B).**

- **Triggers:** `repository_dispatch` with event type `langfuse-prompt-update` — fired by a Langfuse prompt automation when a prompt version gains the `production` label (see [`docs/loop-activation-checklist.md`](docs/loop-activation-checklist.md) for the one-time Langfuse-side setup) — plus manual `workflow_dispatch` (`prompt_name` required, `model` defaults to `claude-sonnet-4-6`) so it's testable without the webhook
- **Behavior:** runs [`scripts/recert_for_prompt.py`](#scriptsrecert_for_promptpy--feedback-loop-prompt-promotion--re-run-the-gate) to map the promoted prompt to its gate target and re-run it with `--ci`. A guard skips dispatches whose payload labels don't include `production` (Langfuse dispatches twice — label gained and label lost)
- **Secrets required:** same four as `certification.yml`

Both live workflows carry a `concurrency` group with `cancel-in-progress`, keyed
on the ref (`certification.yml`) or the changed prompt name (`prompt-recert.yml`).
A burst of label changes in the Langfuse UI would otherwise queue several full
gate runs, each one real LLM spend, all answering a question a newer run has
already superseded.

`recert_for_prompt.py` also writes a verdict to `$GITHUB_STEP_SUMMARY`, so the
outcome is readable in the run summary without opening logs. That matters most
for the **skip** case: a prompt that maps to no gate target exits 0, which renders
as a green check indistinguishable from "the gate ran and passed". The summary
states explicitly that no gate was run and the job makes no claim about quality.

The workflows are deployment-agnostic — point `LANGFUSE_BASE_URL` at Cloud or at a self-hosted Langfuse reachable from GitHub Actions runners (e.g. a publicly-resolvable hostname, a VPN/tunnel, or a self-hosted runner inside your network).

## What governance frameworks expect

Nothing here issues a certificate. What it produces is the *evidence* a governance
process runs on — versioned runs, traces, scores, documented human review, and
monitoring history. That evidence maps cleanly onto what the major frameworks ask
for:

| Framework | What it expects | What the gate provides |
|---|---|---|
| **Fed SR 26-2 / OCC 2026-13** (US model risk) | Generative and agentic AI are excluded from formal scope, but banks must govern them with appropriate controls | A design reference for an internal LLM gate — versioned runs, evaluators, and thresholds |
| **PRA SS1/23** (UK model risk) | Governance, validation, and ongoing monitoring for AI/ML | Versioned gate runs as validation and revalidation evidence |
| **EU AI Act** | Logging, human oversight, and post-market monitoring for high-risk uses such as creditworthiness assessment | Traces for logging, annotation queues for documented human review, `monitor_production.py` for the post-market process |
| **DORA** (EU ICT risk) | Operational evidence on model API availability, degradation, and incidents | Trace and monitoring history of API behaviour and failures |
| **NIST AI RMF** (voluntary) | Documented testing before deployment and monitoring in use | Datasets, evaluators, gates, and run history for *Measure*; review queues for *Manage* |

Your institution keeps the parts that must stay with people: applicability
decisions, policy, independent challenge, approvals, and legal compliance. The
pipeline supplies the reproducible evidence underneath those decisions — it does not
make them.

## Adapting it to your organization

The FinanceBench + Financial PhraseBank setup is a runnable reference. To adapt it:

### Bring your own golden datasets

Replace FinanceBench with your institution's own golden Q&A pairs. `setup_datasets.py`
accepts any JSON file with input, expected output, and metadata fields:

```json
[
  {
    "question": "What was the total revenue for FY2023?",
    "answer": "$52.6 billion",
    "justification": "From the income statement, line: Total Revenue"
  }
]
```

Then load it with `setup_datasets.py` or use the Langfuse SDK directly:

```python
from langfuse import get_client
langfuse = get_client()
langfuse.create_dataset(name="my-custom-dataset")
langfuse.create_dataset_item(
    dataset_name="my-custom-dataset",
    input={"question": "What was the total revenue for FY2023?"},
    expected_output={"answer": "$52.6 billion"},
)
```

### Add domain-specific evaluators

Add checks for your terminology, formatting requirements, or regulatory constraints.
Add a function to `evaluators.py` following the Langfuse signature, then import it in
`run_certification.py` and add it to `select_evaluators()`:

```python
from langfuse import Evaluation

def my_custom_evaluator(*, input, output, expected_output, **kwargs):
    # Your evaluation logic here
    score = 1.0 if "some condition" else 0.0
    return Evaluation(name="my_metric", value=score, comment="Reason")
```

### Sweep prompt variants

The `--system-prompt-file` + `--label` pair lets you run the **same model with a
domain-specialized system prompt** through the gate and compare it side-by-side with
the baseline on the dashboard. This is *not* fine-tuning — it's prompt engineering —
but for many enterprise applications the uplift is comparable.

The repo ships one variant: **`prompts/finance_expert.md`** — a senior-financial-analyst system prompt with a 4-step CoT scaffold (identify metric → quote evidence → apply formula → state result) and FinanceBench-specific cautions (units, sign conventions, line-item confusion). On `financebench-sample` it lifts Opus 4.7 from 95% → 100%; on `financebench-v1` it's the difference between FAILED and PASSED for the same model.

```bash
# Baseline
uv run python run_certification.py --dataset certification/financebench-v1 --model claude-opus-4-7

# Finance Expert variant
uv run python run_certification.py --dataset certification/financebench-v1 --model claude-opus-4-7 \
    --system-prompt-file prompts/finance_expert.md --label finance-expert
```

The dashboard groups by `metadata.model`, so the two runs appear as `claude-opus-4-7` and `claude-opus-4-7-finance-expert` — distinct rows for the comparison.

> **Note:** the agent prompts (`usecase-*`) *are* already Langfuse-managed via `setup_prompts.py` — versioned, promotable, and wired to [automatic re-runs of the gate](#scriptsrecert_for_promptpy--feedback-loop-prompt-promotion--re-run-the-gate). The `prompts/finance_expert.md` variant stays file-based by design: it's an experiment-time input you sweep with `--system-prompt-file` + `--label`, not a deployed prompt. Promote it into Langfuse prompt management if it graduates to production tooling.

### Register custom agents and wire the gate into CI

Register your own agent with its steps and gate thresholds in the agent registry, and
integrate gate failures into your build pipeline so model and prompt approvals become
part of the deployment pipeline (see [CI/CD integration](#cicd-integration)).

### Use a custom LLM gateway

```bash
# Via environment variable
export LLM_BASE_URL="https://your-gateway.internal/v1"
export LLM_API_KEY="your-key"

# Or via CLI flag
uv run python run_certification.py --endpoint https://your-gateway.internal/v1 --dataset ...
```

### Expand to more financial datasets

The [Open FinLLM Leaderboard](https://huggingface.co/spaces/TheFinAI/Open-Financial-LLM-Leaderboard) provides 40+ financial datasets. Good next candidates:

| Dataset | Focus | HuggingFace ID |
|---------|-------|----------------|
| FLARE FinQA | Numerical reasoning over financial tables | `ChanceFocus/flare-finqa` |
| FLARE FOMC | Monetary policy stance classification | `ChanceFocus/flare-fomc` |
| Credit Risk (German) | Credit scoring | `ChanceFocus/flare-german` |
| Credit Risk (Taiwan) | Credit risk assessment | `TheFinAI/cra-taiwan` |
| TATQA | Table + text hybrid QA | `ChanceFocus/flare-tatqa` |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Yes | — | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Yes | — | Langfuse project secret key |
| `LANGFUSE_BASE_URL` | No | `https://cloud.langfuse.com` | Langfuse instance URL — Cloud (EU/US) or self-hosted (typically `http://localhost:3000`). See [Choose your Langfuse deployment](#choose-your-langfuse-deployment). |
| `LANGFUSE_HOST` | No | — | Fallback for `LANGFUSE_BASE_URL` (honored by the scripts and portal) |
| `ANTHROPIC_API_KEY` | For Claude models | — | Anthropic API key for Claude models (required for the AI agents and the LLM judge) |
| `LLM_API_KEY` | For OpenAI models | — | OpenAI-compatible API key (falls back to `OPENAI_API_KEY`) |
| `LLM_BASE_URL` | No | `https://api.openai.com/v1` | LLM API base URL |
| `LLM_MODEL` | No | `claude-sonnet-4-6` | Default model to run through the gate |
| `JUDGE_MODEL` | No | `claude-sonnet-4-6` | Model used by LLM-as-a-Judge evaluators |
| `PORTAL_PORT` | No | `8050` | Certification Portal HTTP port |
| `PORTAL_DATASET_PREFIX` | No | `certification/` | Dataset-name prefix the portal discovers in Langfuse |
| `PORTAL_DATASETS` | No | — | Comma-separated dataset list — pins the portal to exactly these (skips discovery) |
| `CERT_MODEL` | No | `claude-haiku-4-5-20251001` | Model for the live pytest gate (`tests/test_certification.py` only) |
| `CERT_THRESHOLD` | No | `0.85` | Threshold for the live pytest gate (`tests/test_certification.py` only) |
| `ADVISORY_TEMPT_NONCOMPLIANT` | No | — | Set to `1` to nudge the `advisory-draft` agent toward prohibited phrasing — demos the hard compliance-FAIL gate |

The OTel/Langfuse flush-tuning variables (`OTEL_BSP_*`, `LANGFUSE_FLUSH_*`) are covered in
[Troubleshooting: hangs on long runs](#troubleshooting-hangs-on-long-runs).

## FAQ

### How does gate scoring work?

The pipeline runs the model under test against every item in a Langfuse dataset, then scores each response with a set of evaluators. Scores are aggregated at the run level, and a **gate** (`certification_gate`) checks whether the primary accuracy metric meets the configured threshold (default: 85%). The run either PASSES or FAILS. The agent gate works the same way but with a **multi-dimensional gate**: every dimension registered for the agent (e.g. accuracy, groundedness, compliance, tool-use correctness) must clear its own threshold at once.

### Can I gate a whole AI agent, not just a model?

Yes — that's [`run_usecase_certification.py`](#gating-the-whole-agent). Three agents ship registered: `10k-analyst` (grounded SEC-filing QA with a calculator tool), `sentiment-triage` (sentiment classification with human routing), and `advisory-draft` (compliance-gated client prose). Each defines its own trace shape and multi-dimensional gate; on the dashboard they appear as `usecase:<name>` rows next to the plain model rows.

### Are the evaluators deterministic or LLM-based?

Both. The pipeline uses **deterministic evaluators** (regex, string matching, number extraction, and a tool-trajectory check for agent runs) for objective metrics and an **LLM-as-a-Judge evaluator** (`groundedness_evaluator`) for subjective quality assessment. Deterministic evaluators are fast, cheap, and reproducible. The LLM judge catches qualitative failures that heuristics miss — like whether the model hallucinated a number that happens to be correct, or whether it actually used the source documents.

### Can I use a different judge model?

Yes. Set the `JUDGE_MODEL` environment variable:

```bash
JUDGE_MODEL=claude-haiku-4-5-20251001 uv run python run_certification.py --dataset ...
```

Using a cheaper/faster judge model reduces cost but may lower evaluation quality. We recommend using a model at least as capable as `claude-sonnet-4-6` for financial evaluations.

### What's the difference between item-level and run-level evaluators?

- **Item-level** evaluators score each dataset item individually (e.g., "did this answer match the expected number?")
- **Run-level** evaluators aggregate across all items (e.g., "what was the average accuracy?" or "did the run pass the gate?")

### What datasets are supported?

Two financial benchmarks plus an embedded adversarial set are included:

| Dataset | Items | Focus |
|---------|-------|-------|
| [FinanceBench](https://huggingface.co/datasets/PatronusAI/financebench) | 10 (sample) / 150 (full) | Financial QA from SEC filings |
| [Financial PhraseBank](https://huggingface.co/datasets/ChanceFocus/en-fpb) | 10 (sample) / ~4850 (full) | Financial sentiment classification |
| `advisory-adversarial` (embedded) | 10 | Briefs that tempt distinct kinds of non-compliant advisory language — demos the hard compliance gate |

You can add custom datasets — see [Adapting it to your organization](#adapting-it-to-your-organization).

## Companion Projects

- [clickhouse-llm-observability](https://github.com/doneyli/clickhouse-llm-observability) - Full LLM observability stack with LibreChat, Langfuse, and ClickHouse (monitoring, tracing, debugging)

## References

- [Langfuse Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
- [Langfuse Datasets](https://langfuse.com/docs/evaluation/experiments/datasets)
- [Langfuse Custom Scores](https://langfuse.com/docs/scores/custom)
- [FinanceBench Paper](https://arxiv.org/abs/2311.11944)
- [Open FinLLM Leaderboard](https://huggingface.co/spaces/TheFinAI/Open-Financial-LLM-Leaderboard)

## License

Apache 2.0
