# AGENTS.md — operating guide for coding agents

This repo is a **deployment gate** for LLMs and AI agents in financial services:
it runs a model or a multi-step agent against golden datasets, scores every
output, and produces a PASS/FAIL verdict with reviewable evidence in Langfuse.
(See [README.md](README.md) for the full story and the
[companion blog post](https://langfuse.com/blog/2026-07-15-llm-certification-financial-services).)

This file is the **golden path** to stand it up end to end. Follow it top to
bottom. Two steps require a human — they are marked **`HUMAN STEP`** and you
cannot complete them autonomously (they mint credentials).

## TL;DR

```bash
make install       # 1. Python deps
make preflight     # 2. go/no-go: env, credentials, live Langfuse connectivity
make seed          # 3. load sample data + register score configs, queues, prompts
make gate          # 4. run the model gate         -> PASS/FAIL in Langfuse + stdout
make agent-gate    # 4'. run the multi-dimensional agent gate (10k-analyst)
make portal        # 5. build + launch the dashboard on http://localhost:8050
```

`make quickstart` chains steps 1–4 (it stops before the long-running portal).
Run `make help` to list every target.

## Before anything: two `HUMAN STEP`s

The gate talks to Langfuse and to an LLM. Neither credential can be created by an
agent. **Ask the human to provide both, then continue.**

1. **`HUMAN STEP` — Langfuse project + API keys.** Pick one:
   - **Cloud (fastest):** sign up at <https://cloud.langfuse.com> (or the US region),
     create an *Organization → Project*, then **Settings → API Keys → Create**.
   - **Self-hosted:** run `make up` (starts the Docker stack), open
     <http://localhost:3000>, sign up, create an Organization + Project, and create
     API keys. See [selfhost/README.md](selfhost/README.md).
2. **`HUMAN STEP` — an LLM API key.** `ANTHROPIC_API_KEY` for Claude models (the
   default `claude-sonnet-4-6` is Claude-native and the AI agents require it), or
   `LLM_API_KEY` / `OPENAI_API_KEY` for an OpenAI-compatible endpoint.

Put both into `.env`:

```bash
cp .env.example .env    # then edit: uncomment exactly ONE LANGFUSE_BASE_URL, paste the keys
```

`make preflight` confirms all of this is in place (and that Langfuse actually
answers) before you run anything. **Do not proceed past a failing preflight.**

## The end-to-end path, with preconditions and success checks

| Step | Command | Precondition | Success check |
|---|---|---|---|
| Install | `make install` | Python ≥ 3.11 (`uv`, or `PY=python` for pip) | deps installed (`uv sync`, or `pip install -r requirements.txt`) |
| Preflight | `make preflight` | `.env` filled (both `HUMAN STEP`s done) | `RESULT: READY`, exit 0 |
| Seed | `make seed` | preflight READY | scripts print created datasets/configs/queues/prompts |
| Model gate | `make gate` | seed done | stdout shows `PASS`/`FAIL`; run appears in Langfuse → Datasets → Runs |
| Agent gate | `make agent-gate` (dataset auto-routes per `USE_CASE`) | seed done + `ANTHROPIC_API_KEY` | per-dimension PASS/FAIL; `usecase:<name>` row |
| Portal | `make portal` | Node 20+/npm 10+ (frontend builds once) | dashboard at <http://localhost:8050> |
| Evidence | `make export` | a completed run | markdown evidence pack on stdout |

**Ordering that matters** (already encoded in `make seed`, but do not reorder if
you run scripts by hand):
- `setup_score_configs.py` **before** `setup_annotation_queues.py` (queues bind to score configs).
- Datasets must be loaded **before** a gate run (`make seed` loads all three golden sets).
- Each agent gates against its **own** dataset — `make agent-gate` auto-routes it:
  `10k-analyst`→`certification/financebench-sample`, `sentiment-triage`→`certification/fpb-sample`,
  `advisory-draft`→`certification/advisory-adversarial` (advisory-draft can *only* pass on the
  adversarial set — never FinanceBench). Override with `DATASET=...` if needed.
- The portal frontend must be **built** before launch (the `portal` target builds it once into `portal/frontend/dist`).

## Deployment choice: Cloud vs self-hosted

The code is identical for both — only `LANGFUSE_BASE_URL` changes.
- **Cloud:** `LANGFUSE_BASE_URL=https://cloud.langfuse.com` (EU) or `https://us.cloud.langfuse.com` (US).
- **Self-hosted:** `make up`, then `LANGFUSE_BASE_URL=http://localhost:3000`.

## Interacting with Langfuse: the CLI and the agent skill (recommended for agents)

Langfuse ships two tools that make an agent far better at working with a live
Langfuse deployment — inspecting state, verifying a run, iterating on datasets and
prompts — **without writing Python**. Both authenticate with the **same
`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`** you already
put in `.env`, so there is no extra login step. The bootstrap above does not
depend on them, but you should use them.

**Langfuse CLI** ([docs](https://langfuse.com/docs/api-and-data-platform/features/cli)) —
a thin wrapper over the full Langfuse REST API:

```bash
npx langfuse-cli api __schema                          # list every resource
npx langfuse-cli --env .env api datasets list          # confirm the golden datasets loaded
npx langfuse-cli --env .env api traces list --limit 10 # see recent gate-run traces
npx langfuse-cli --env .env api prompts list           # inspect managed prompts
```

`--env .env` loads the repo's credentials from the file (otherwise the CLI reads
the `LANGFUSE_*` vars from your shell). Install once with `npm i -g langfuse-cli`
if you prefer `langfuse …` over `npx langfuse-cli …`.

**Langfuse agent skill** ([docs](https://langfuse.com/docs/api-and-data-platform/features/agent-skill)) —
an Agent-Skills-standard skill that teaches an agent Langfuse best practices and
drives the CLI under the hood (query traces, build datasets, migrate prompts into
prompt management, pull docs). Works with Claude Code, Cursor, Windsurf, and other
compatible agents. Add it with:

```bash
npx skills add langfuse/skills --skill "langfuse"
# or print the skill file from the CLI you already have:
langfuse get-skill
```

Use these to answer "did the gate run actually land?" — e.g. after `make gate`,
`npx langfuse-cli --env .env api datasets list` should show
`certification/financebench-sample`, and its latest run should carry the new scores.

## Gotchas (read before editing env or running long jobs)

- **`.env` loading.** Every script loads `.env` with `override=True` (the file
  wins over shell vars). A fresh `cp .env.example .env` is a plain file; if you
  adopt the optional dual-profile setup, `bash scripts/use_env.sh {status|cloud|local|both}`
  turns `.env` into a symlink to `.env.local`/`.env.cloud` — switch profiles with
  that script rather than editing the symlink target directly.
- **Re-running `setup_datasets.py` duplicates items** (items have no stable ids).
  Seed once; use `--dry-run` to preview. `promote_trace_to_dataset.py` upserts and
  is safe to re-run.
- **Long local runs can hang** (OTel queue saturation against a slow local
  Langfuse). Stick to the `-sample` datasets locally; the full 150-item FinanceBench
  run is happier on Cloud. See README "Troubleshooting: hangs on long runs".
- **CI gating:** `make gate` and `make agent-gate` report PASS/FAIL but always
  exit 0. To fail a pipeline on a bad gate, run the script with `--ci` (exits 1
  unless the gate passes) — or `make ci-gate`, which is the agent gate with `--ci`.
- **Thresholds are not in the code:** every gate bar lives in
  `cicd/thresholds.json`. Edit that file, not the agent modules;
  `tests/test_gate_thresholds.py` fails on drift. A missing entry raises rather
  than defaulting, because an empty gate would certify everything as PASSED.
- **Agent gate is Claude-only** (agents call the native Anthropic SDK); the model
  gate accepts any OpenAI-compatible endpoint.

## Where things live

| Area | Path |
|---|---|
| Model gate runner | `run_certification.py` |
| Agent gate runner + registry | `run_usecase_certification.py`, `agents/` |
| Evaluators (deterministic + LLM judge) + gate logic | `evaluators.py` |
| Setup scripts | `setup_datasets.py`, `setup_score_configs.py`, `setup_annotation_queues.py`, `setup_prompts.py` |
| Feedback loop | `monitor_production.py`, `promote_trace_to_dataset.py`, `scripts/recert_for_prompt.py` |
| Portal (FastAPI + React) | `portal/` |
| Self-host stack | `selfhost/` |
| Preflight | `scripts/preflight.py` |
| Deep-dive docs | `docs/` (start with `docs/ai-engineering-loop.md`) |

## Verify the whole thing worked

1. `make preflight` → `RESULT: READY`.
2. `make gate` → a `PASS` or `FAIL` verdict, and a run under
   **Langfuse → Datasets → `certification/financebench-sample` → Runs**.
3. `make portal` → <http://localhost:8050> shows the gate matrix with your run as a row.
4. `make test` → the offline suite passes (no credentials needed; good for a sanity check any time).
