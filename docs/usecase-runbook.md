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

> **Status (2026-11-04):** all three agents (10k-analyst, sentiment-triage,
> advisory-draft) are on `main`, running on the Langfuse Python SDK v5 against
> Langfuse v4. `make test` passes 201 offline tests. Every step below was
> re-run on the Cloud US demo project this date unless it says otherwise.

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

# Stage 4 — Score configs (pass/fail scores are BOOLEAN)  [now]
uv run python setup_score_configs.py

# Stage 6 — Human review (Annotation Queues)             [now]
uv run python setup_annotation_queues.py

# Optional — production monitoring inside Langfuse, and the gate dashboard
uv run python setup_evaluators.py     # managed evaluators + rules (rules created disabled)
uv run python setup_dashboards.py     # "Deployment gate — quality & cost"
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
| Nested agent trace (the showcase) | Langfuse UI → **Experiments** → the run (URL printed by the runner) → open an item → span tree + **Agent Graph** tab |
| Calculator tool I/O on numerical items | the `calculate` tool span's input/output |
| Per-dimension PASS/FAIL | `certification_result` (BOOLEAN) score comment on the experiment (run-level score) |
| Scores per prompt version | Langfuse UI → **Prompts** → `usecase-10k-analyst-compose` → **Metrics** |
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

1. **Monitor flags it.** The managed evaluators (`setup_evaluators.py`) score every
   production root observation; a prohibited phrase gives
   `regulatory_compliance = false`. Without managed evaluators, run the cron
   fallback: `uv run python monitor_production.py --hours 24 --queue-violations`.
2. **A human reviews it.** Open the trace (or **Annotation Queues → Certification
   Review**) and write the correct answer as a **Correction** on the trace.
3. **Promote it** (human-gated: the Correction becomes the expected answer;
   `--expected` overrides it; with neither the item is flagged
   `needs_expected_review`):

   ```bash
   uv run python promote_trace_to_dataset.py \
       --dataset certification/financebench-sample --trace-id <trace id>
   ```

4. **Re-run the gate against a reviewed baseline.** The next run regression-tests
   that real scenario, and `--baseline` names any case that passed in the
   baseline but fails now:

   ```bash
   uv run python run_usecase_certification.py --use-case 10k-analyst \
       --dataset certification/financebench-sample --ci \
       --baseline <name of a reviewed experiment>
   ```

---

## 6. Demo script (the 10-minute story)

`scripts/demo_usecase.sh` (`make demo`) provisions everything and runs one gate;
`make portal` serves the dashboard on <http://localhost:8050>. For the
**loop-shaped** version of this narration (Trace → Monitor → Build Datasets →
Experiment → Evaluate), see
[`ai-engineering-loop.md` → Demoing this](ai-engineering-loop.md#demoing-this).

```bash
bash scripts/demo_usecase.sh                                          # 10-K agent, Sonnet
MODEL=claude-haiku-4-5-20251001 bash scripts/demo_usecase.sh          # same agent, Haiku
USE_CASE=advisory-draft ADVISORY_TEMPT_NONCOMPLIANT=1 bash scripts/demo_usecase.sh   # the FAIL
```

**Before you present**
- Pre-run the three commands above so every view has data. Runs take a few
  minutes each, so don't run them live.
- Open in tabs: the portal, Langfuse **Experiments**, the
  "Deployment gate — quality & cost" dashboard, and **Prompts →
  usecase-10k-analyst-compose → Metrics**.
- For the production beat (step 6), the demo project has test traces in the
  `production` environment tagged `deployment-gate-smoke-test`, including one
  with a prohibited phrase. A fresh project needs production traffic first.

**Narration**

1. **Reframe.** "We're not gating a model in isolation. We're gating a whole
   *AI agent* — this 10-K analyst — as a complete system, before it ships."
2. **Show the trace.** Langfuse → **Experiments** → open the 10-K run → one item.
   - The **Agent Graph** tab shows the path: plan → retrieve-evidence →
     **calculate** → compose-answer.
   - In the tool span, point at `6489/((253+282)/2) = 24.26`: "It didn't do the
     math in its head — it used a tool. That's auditable."
3. **Show the gate.**
   - The run-level `certification_result` is a pass/fail score (`true`), and its
     comment lists every bar: numerical accuracy ≥ 85%, groundedness ≥ 80%,
     compliance = 100%, tool use ≥ 90%.
   - "PASS means all four cleared, not an average of them. The bars live in
     `cicd/thresholds.json`, so loosening one is a reviewed diff."
4. **Swap the model.** Show the Haiku run of the same agent next to Sonnet in
   Langfuse's experiment comparison. On 2026-11-04 Haiku PASSED: 90% numerical
   accuracy, 96.5% groundedness, compliance and tool use 100%. "The gate decides
   whether the cheaper model is good enough for this job — with evidence, not
   opinion."
   - Don't claim a lift here: on this 10-item sample, Haiku *without* the agent
     (the model gate) also scores 90–100% numerical accuracy in current runs. The
     earlier ~60% figure (README, deck) is not reproduced by current data.
5. **Show a FAIL.** The advisory-draft run with `ADVISORY_TEMPT_NONCOMPLIANT=1`:
   - The draft is accurate and grounded, but one prohibited phrase drops
     `regulatory_compliance` below 100%, so the gate FAILS.
   - "Accurate, but it can't ship." (This path has not been re-run since the
     SDK v5 upgrade; pre-run it as above.)
   - Alternative: a stricter profile (numerical accuracy ≥ 95% for a high-stakes
     desk) fails the 90% run and names the dimension.
6. **Production.** "The gate doesn't stop at release."
   - The same compliance and completeness checks run *inside Langfuse* on
     production traffic as managed evaluators. Their code is generated from the
     gate's own `evaluators.py`, so they can't drift from the gate.
   - Open the `deployment-gate-smoke-test` trace with "guaranteed returns":
     `regulatory_compliance = false`.
   - Show the dashboard's **compliance true-rate**. An alert on it notifies Slack
     when it drops below 100%.
7. **Close the loop.**
   - A reviewer writes a **Correction** on that trace, and
     `promote_trace_to_dataset.py` turns it into a golden case.
   - The next gate run with `--baseline` reports any **newly failing case**,
     which an average would hide.
   - Prompt changes close the other loop: **Prompts → Metrics** shows scores,
     latency and cost per prompt version, so promoting a version and re-running
     the gate is a measured comparison.
8. **The matrix.** Back on the portal: one row per dataset × model or agent, each
   badge judged against the bars that run recorded. "A failing number caught here
   costs nothing; caught after deployment it's a reportable incident."

**Soft spots to expect in Q&A**
- The sample is 10 items, so one answer moves a score by 10 points.
- "Retrieve" means extraction from the supplied filing excerpts, not search.
- Each bar is itself an average across items. That's why `--baseline` checks
  cases individually.
- Even without the tempting prompt, the advisory agent is not fully stable on
  the adversarial set: it FAILED on 2026-09-24 (compliance 90%, one item) and
  PASSED on 2026-11-04 (100%). The gate catching that is the point; the
  hard 100% bar is deliberate (see `cicd/thresholds.json`).

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
