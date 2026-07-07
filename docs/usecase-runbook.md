# Use-Case Certification — Runbook & Demo

Operational runbook for certifying a **use case** (agent) end-to-end, and the
script for demoing it. Pairs with
[`usecase-architecture.md`](usecase-architecture.md) (the lifecycle map) and
[`usecase-certification.md`](usecase-certification.md) (the spec).

> **Status (PR #12 / issue #8):** the shared **foundation** is in place — runner,
> multi-dimensional gate, trajectory evaluator, score configs, prompt templates,
> `cert_common` plumbing. The **agents themselves are pending** (#9 10k-analyst,
> #10 sentiment-triage, #11 advisory-draft). Steps below are marked **[now]** if
> they work today or **[needs #9]** if they require the first agent. The demo
> script (`scripts/demo_usecase.sh`) prints loud `PENDING` markers where an agent
> is not yet implemented, so it never silently looks like it succeeded.

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

## 3. List use cases  [now]

```bash
uv run python run_usecase_certification.py --list
```

Today every use case prints `[pending]` until its agent module lands. Once #9 is
merged, `10k-analyst` flips to `[registered]` with its gate dimensions shown.

---

## 4. Certify a use case  [needs #9 for a real run]

```bash
uv run python run_usecase_certification.py \
    --use-case 10k-analyst \
    --dataset certification/financebench-sample \
    --model claude-sonnet-4-6 \
    --queue-failures
```

Until #9 lands this exits 1 with guidance (foundation present, agent pending) —
intended. After #9:

1. The agent runs once per item, emitting a nested trace
   (plan → retrieve → calculate → compose).
2. Item evaluators score each trace; run evaluators aggregate.
3. `usecase_certification_gate` writes `certification_result` = PASS only if
   **every** dimension clears its threshold.
4. `--queue-failures` routes hard-failing items to the annotation queue.

**CI gate:** add `--ci` to exit 1 on FAIL (drop-in for the GitHub Actions job).

---

## 5. Inspect results

| What | Where |
|---|---|
| Nested agent trace (the showcase) | Langfuse UI → **Datasets** → run → open an item → span tree |
| Calculator tool I/O on numerical items | the `calculate` tool span's input/output |
| Per-dimension PASS/FAIL | `certification_result` score comment on the first trace |
| Certification matrix (PASS/FAIL badge) | Portal `/` → row `usecase:10k-analyst` |
| Run history / trend | Portal `/history/certification/financebench-sample` |
| Items needing human sign-off | Langfuse UI → **Annotation Queues** → Certification Review |

Export a compliance report:

```bash
uv run python export_results.py --dataset certification/financebench-sample
```

---

## 6. Demo script (the 5-minute story)

`scripts/demo_usecase.sh` walks the full lifecycle. It runs setup, lists use
cases, and attempts the certification run — printing a loud `PENDING #9` banner
if the agent is not yet implemented rather than pretending to succeed.

```bash
bash scripts/demo_usecase.sh
```

**Narration when demoing live (after #9):**

1. **Reframe** — "We're not certifying a model; we're certifying a *use case* —
   this 10-K analyst agent, as a whole system."
2. **Show the trace** — open one item. Walk the span tree: it *planned*, *pulled
   the line items*, *called the calculator* (point at the tool span's
   `6489/((253+282)/2) = 24.26`), then *composed a cited answer*. "It didn't do
   the math in its head — it used a tool. That's auditable."
3. **Show the gate** — the `certification_result` comment lists every dimension:
   accuracy, groundedness, compliance, tool-use. "PASS means all of them cleared,
   not just accuracy."
4. **The whole-system lift (observed)** — rerun on Haiku
   (`--model claude-haiku-4-5-20251001`). On the 10-item sample it also PASSES
   (~90% numerical accuracy). "Model certification alone shows Haiku at ~60% raw
   numerical accuracy (see README FAQ) — but the *use case* wraps it in a
   calculator tool, so the system is certifiable even on a cheap model. We're
   certifying the system, not the model." (Verified runs: 2026-06-05, Sonnet and
   Haiku both PASSED.)
5. **Show a FAIL** — the honest FAIL paths on this pipeline:
   - **Compliance hard-gate** — the Advisory Drafting agent (#11) with an
     adversarial item containing prohibited language: `regulatory_compliance`
     drops below 1.00 → gate FAILS even if accuracy is perfect. (The clearest
     "accurate but uncertifiable" story.)
   - **Stricter threshold profile** — raise `numerical_accuracy` to ≥0.95 for a
     high-stakes use case; the 90%-accurate run then FAILS, and the breakdown
     names the dimension. (Earlier in development, before the trajectory rule was
     refined, Sonnet also FAILED on `tool_use_correctness` when judgment questions
     were wrongly required to use the calculator — a good example of the gate
     catching a trajectory problem.)
6. **Show the dashboard** — the Portal row shows the PASS/FAIL badge per run.

---

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Run hangs near the tail on local Langfuse | OTel queue saturation — same as model cert; the runner sets safe OTel defaults. See README "Hangs on long runs". |
| `certification_result` shows a dimension at 0% you didn't expect | a gate dimension had **no** item scores (averages to 0 → fails). Confirm the evaluator for that dimension is in the agent's `ITEM_EVALUATORS`. |
| Agent trace has no nested spans | the span-nesting assumption (architecture §5) — verify `start_as_current_observation` nests inside `run_experiment`; thread `trace_context` if not. |
| `--use-case X is not implemented yet` | expected pre-#9/#10/#11; the foundation is present, the agent is not. |

---

## 8. Rollback / safety

- This workstream **adds** files and is additive to `evaluators.py` /
  `setup_*.py`; the model-cert path (`run_certification.py`) is unchanged in
  behavior (refactored to share `cert_common`).
- Prompts are versioned: to revert an agent prompt, move the `production` label to
  a previous version in the Langfuse UI — no code change.
- Score configs and prompts are idempotent; re-running setup is safe.
