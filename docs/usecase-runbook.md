# AI Agent Deployment Gate — Runbook & Demo

Operational runbook for running the deployment gate on an **AI agent** end-to-end,
and the script for demoing it. Pairs with
[`ai-engineering-loop.md`](ai-engineering-loop.md) (the objective / loop story),
[`usecase-architecture.md`](usecase-architecture.md) (the lifecycle map) and
[`usecase-certification.md`](usecase-certification.md) (the spec).

> **On naming:** the concept here is a **deployment gate**; "certification"
> survives only as the internal process name and inside literal code identifiers
> (`usecase_certification_gate`, `certification_result`, the `certification/`
> dataset prefix). Nothing here is a regulatory certification.

> **Status:** the shared foundation **and** all three agents (10k-analyst #9,
> sentiment-triage #10, advisory-draft #11) are on `main` and verified together —
> `run_usecase_certification.py --list` shows all three `[registered]` and the 52
> offline unit tests pass. Every step below works today.

---

## 1. Prerequisites

- Python 3.11+, `uv sync` (or `pip install -r requirements.txt`).
- A Langfuse instance (Cloud or self-hosted) — see the main README.
- Env (`.env`): `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`,
  and `ANTHROPIC_API_KEY` (agents call Claude via the native Anthropic SDK).

---

## 2. One-time setup — wire the eval lifecycle

Each command provisions one lifecycle stage in Langfuse. Run in order; all are
idempotent.

```bash
# Stage 1 — Golden data (Datasets)          [now]
uv run python setup_datasets.py --dataset financebench --sample
uv run python setup_datasets.py --dataset fpb --sample

# Stage 2 — Prompt management (Prompts)     [now]
#   registers financial-qa/-sentiment AND the use-case agent templates
uv run python setup_prompts.py

# Stage 4 — Score configs (incl. tool_use_correctness)   [now]
uv run python setup_score_configs.py

# Stage 6 — Human review (Annotation Queues)             [now]
uv run python setup_annotation_queues.py
```

**Verify in the Langfuse UI:**
- **Datasets** → `certification/financebench-sample`, `certification/fpb-sample`
- **Prompts** → `usecase-10k-analyst-compose`, `usecase-sentiment-classify`,
  `usecase-advisory-analyze`, `usecase-advisory-draft` (all `production`-labelled).
  Note: the 10-K Analyst's plan/extract prompts are code-owned (parsed JSON), so
  only its free-form `compose` step is managed here.
- **Settings → Score Configs** → `tool_use_correctness` present
- **Annotation Queues** → `Certification Review`

---

## 3. List AI agents  [now]

```bash
uv run python run_usecase_certification.py --list
```

All three AI agents print `[registered]` with their gate dimensions. (On a branch
where an agent module is absent, that agent prints `[pending]` instead — the
foundation tolerates missing agents.)

---

## 4. Run the deployment gate on an agent  [now]

```bash
uv run python run_usecase_certification.py \
    --use-case 10k-analyst \
    --dataset certification/financebench-sample \
    --model claude-sonnet-4-6 \
    --queue-failures
```

This performs the run:

1. The agent runs once per item, emitting a nested trace
   (plan → retrieve → calculate → compose).
2. Item evaluators score each trace; run evaluators aggregate.
3. `usecase_certification_gate` writes `certification_result` = PASS only if
   **every** dimension clears its threshold — a multi-dimensional gate, not a
   single score.
4. `--queue-failures` routes hard-failing items to the annotation queue.

**CI gate:** add `--ci` to exit 1 on FAIL (drop-in for the GitHub Actions job).

---

## 5. Inspect results

| What | Where |
|---|---|
| Nested agent trace (the showcase) | Langfuse UI → **Datasets** → run → open an item → span tree |
| Calculator tool I/O on numerical items | the `calculate` tool span's input/output |
| Per-dimension PASS/FAIL | `certification_result` score comment on the first trace |
| Gate matrix (PASS/FAIL badge) | Portal `/` → row `usecase:10k-analyst` |
| Run history / trend | Portal `/history/certification/financebench-sample` |
| Items needing human sign-off | Langfuse UI → **Annotation Queues** → Certification Review |

Export a compliance report:

```bash
uv run python export_results.py --dataset certification/financebench-sample
```

### Close the loop — promote a production failure into golden data

The observation → development feedback edge (see
[`ai-engineering-loop.md` → Edge A](ai-engineering-loop.md#closing-the-loop-what-is-wired-vs-open)):

```bash
# 1) Monitor flags live compliance violations and routes them to the review queue
uv run python monitor_production.py --hours 24 --tags production --queue-violations

# 2) A human reviews the queued traces in Langfuse (Annotation Queues → Certification
#    Review) and decides the correct answer.

# 3) Promote a reviewed trace into the golden dataset (human-gated: supply the
#    correct answer, or leave it flagged needs_expected_review to fill in the UI)
uv run python promote_trace_to_dataset.py \
    --dataset certification/financebench-sample \
    --from-queue --expected "The FY2019 fixed asset turnover is 24.26"

# 4) Re-certify — the next run now regression-tests that real scenario
uv run python run_usecase_certification.py --use-case 10k-analyst \
    --dataset certification/financebench-sample --ci
```

---

## 6. Demo script (the 5-minute story)

`scripts/demo_usecase.sh` walks the full lifecycle: setup, lists the AI agents, and
runs the deployment gate. (If run on a branch where the chosen agent is absent it
prints a loud `PENDING` banner rather than pretending to succeed — on `main` all
three run.)

```bash
bash scripts/demo_usecase.sh
```

> For the **loop-shaped** version of this narration (framed as Trace → Monitor →
> Build Datasets → Experiment → Evaluate), see
> [`ai-engineering-loop.md` → Demoing this](ai-engineering-loop.md#demoing-this).

**Narration when demoing live:**

1. **Reframe** — "We're not gating a model in isolation; we're gating a whole
   *AI agent* — this 10-K analyst, as a complete system."
2. **Show the trace** — open one item. Walk the span tree: it *planned*, *pulled
   the line items*, *called the calculator* (point at the tool span's
   `6489/((253+282)/2) = 24.26`), then *composed a cited answer*. "It didn't do
   the math in its head — it used a tool. That's auditable."
3. **Show the gate** — the `certification_result` comment lists every dimension:
   accuracy, groundedness, compliance, tool-use. "This is a multi-dimensional
   gate — PASS means all of them cleared, not just accuracy. That's the reviewable
   evidence a human signs off on."
4. **The whole-system lift (observed)** — rerun on Haiku
   (`--model claude-haiku-4-5-20251001`). On the 10-item sample it also PASSES
   (~90% numerical accuracy). "Gating the model alone shows Haiku at ~60% raw
   numerical accuracy (see README FAQ) — but the *agent* wraps it in a
   calculator tool, so the system passes the gate even on a cheap model. We're
   gating the system, not the model." (Verified runs: 2026-06-05, Sonnet and
   Haiku both PASSED.)
5. **Show a FAIL** — the honest FAIL paths this gate produces:
   - **Compliance hard-gate** — the Advisory Drafting agent (#11) with an
     adversarial item containing prohibited language: `regulatory_compliance`
     drops below 1.00 → gate FAILS even if accuracy is perfect. (The clearest
     "accurate but can't clear the gate" story.)
   - **Stricter threshold profile** — raise `numerical_accuracy` to ≥0.95 for a
     high-stakes agent; the 90%-accurate run then FAILS, and the breakdown
     names the dimension. (Earlier in development, before the trajectory rule was
     refined, Sonnet also FAILED on `tool_use_correctness` when judgment questions
     were wrongly required to use the calculator — a good example of the gate
     catching a trajectory problem.)
6. **Show the dashboard** — the Portal row shows the PASS/FAIL badge per run.
   "A failing number caught here on the dashboard costs nothing; caught after
   deployment it's a reportable incident — that's why the gate runs before
   sign-off, because manual approval alone can't keep pace with deployment
   velocity."

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Run hangs near the tail on local Langfuse | OTel queue saturation — same as the model gate; the runner sets safe OTel defaults. See README "Hangs on long runs". |
| `certification_result` shows a dimension at 0% you didn't expect | a gate dimension had **no** item scores (averages to 0 → fails). Confirm the evaluator for that dimension is in the agent's `ITEM_EVALUATORS`. |
| Agent trace has no nested spans | the span-nesting assumption (architecture §5) — verify `start_as_current_observation` nests inside `run_experiment`; thread `trace_context` if not. |
| `--use-case X is not implemented yet` | you are on a branch where that agent module is absent; on `main` all three are present. |

---

## 8. Rollback / safety

- This workstream **adds** files and is additive to `evaluators.py` /
  `setup_*.py`; the model-gate path (`run_certification.py`) is unchanged in
  behavior (refactored to share `cert_common`).
- Prompts are versioned: to revert an agent prompt, move the `production` label to
  a previous version in the Langfuse UI — no code change.
- Score configs and prompts are idempotent; re-running setup is safe.
