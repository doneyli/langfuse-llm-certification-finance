# The AI Engineering Loop — this use case, end to end

> **Read this first if you want the *objective*, not the plumbing.** The
> [architecture doc](usecase-architecture.md) maps components; the
> [spec](usecase-certification.md) is the implementation; the
> [runbook](usecase-runbook.md) is how to operate it. This doc is the **story**:
> how a financial-services LLM use case moves through
> [Langfuse's AI Engineering Loop](https://langfuse.com/academy/ai-engineering-loop)
> — Trace → Monitor → Build Datasets → Experiment → Evaluate — and keeps moving,
> because a use case is never "certified once." It is certified *and re-certified*
> as prompts, models, and the world change.

## The objective

Model-risk feedback reframed the problem: stop certifying *models* in
isolation, start certifying *use cases* — the whole agent (LLM calls + tools +
retrieval) deployed for a business purpose. But "certify the use case" is only
half the shift. The other half is **continuity**. A model-risk sign-off that is
true on the day of the run and stale a week later is worthless once the prompt is
edited, the model is swapped, or production drifts.

The AI Engineering Loop is the answer to *"how do you stay certified?"* It is a
**cycle**, not a checklist: production behavior feeds measurement, measurement
feeds test data, test data feeds experiments, experiments feed the ship/no-ship
decision, and the shipped change flows back into production — where the loop
starts again. This repo implements that loop for three finance agents. The
**10‑K Filing Analyst** is the worked example throughout.

Langfuse states the core reason plainly: *"you cannot unit-test your way to
confidence"* with probabilistic LLM outputs — *"systematic observation, learning,
and experimentation are required instead."* That is why this is a loop.

## The loop in one picture

```
                         ┌───────────────────────────────────────┐
                         │  a change ships (new prompt version,    │
                         │  new model, new retrieval strategy)     │
                         └───────────────┬─────────────────────────┘
                                         │ deploy
                                         ▼
        ┌────────────┐   full request path (prompts, evidence,
        │ 1. TRACE   │   tool calls, output, latency, cost)
        │            │   agents/base.py → nested span tree per item
        └─────┬──────┘
              ▼
        ┌────────────┐   score live traffic; surface what deserves
        │ 2. MONITOR │   attention (compliance, completeness)
        │            │   monitor_production.py · UI LLM-as-judge
        └─────┬──────┘
              │  ┌─────────────────────────────────────────────┐
              │  │  FEEDBACK EDGE (partially wired — see §Close) │
              │  │  a surfaced failure should become golden data │
              ▼  ▼
        ┌────────────────┐   real failures + designed edge cases
        │ 3. BUILD        │   → repeatable test cases
        │    DATASETS     │   setup_datasets.py · Langfuse Datasets
        └─────┬───────────┘
              ▼
        ┌────────────┐   change ONE variable vs a stable baseline
        │ 4.         │   (prompt / model / retrieval)
        │ EXPERIMENT │   run_usecase_certification.py · run_experiment
        └─────┬──────┘
              ▼
        ┌────────────┐   ship or not? multi-dimensional gate:
        │ 5. EVALUATE│   accuracy AND grounded AND compliant AND
        │            │   right tool-path — all must pass
        └─────┬──────┘   evaluators.py · usecase_certification_gate
              │
              └──────────────▶ ship the winner ──▶ back to TRACE (top)
                              (prompt-promotion → auto-recert:
                               the GitHub-integration edge — see §CI/CD)
```

Two edges *close* the loop. Both are called out honestly in
[Closing the loop](#closing-the-loop-what-is-wired-vs-open) — one is partially
wired, one is not wired yet. Everything **inside** the cycle runs today on this
branch (all three agents assembled — see [Demoing this](#demoing-this)).

---

## Stage 1 — Trace

> *Academy:* "Capture the full path of a request, including prompts, retrieved
> context, tool calls, outputs, latency, and cost."

**This use case.** The 10‑K Analyst is not one LLM call — it is a four-step agent,
and each step is a span, so the trace *is* the audit artifact a model-risk
reviewer opens:

```
usecase:10k-analyst                                   (trace = one dataset item)
├── plan               generation   "CALCULATE: revenue / avg PP&E"
├── retrieve-evidence  span          └─ extract-operands  generation → {operands, citations}
├── calculate          tool          in "6489/((253+282)/2)"  out 24.26
└── compose-answer     generation    grounded, cited answer
```

`agents/base.py` provides `traced_generation` / `traced_span` / `traced_tool`;
the calculator step matters most — *"it didn't do the math in its head, it used a
tool, and here is the exact expression."* That is the difference between a plausible
answer and an auditable one. (Sentiment-triage emits `classify → rationale →
route`; advisory-draft emits `analyze → draft → compliance-self-check`.)

**Langfuse primitive:** Observations (generation / span / tool) nested under one
trace per item. **See it:** Langfuse UI → **Datasets** → run → open an item.

## Stage 2 — Monitor

> *Academy:* "Track how the system behaves over time and surface the traces that
> deserve attention."

**This use case.** `monitor_production.py` runs on a schedule against *live*
traffic (not the eval set): it fetches recent traces, runs the deterministic
`regulatory_compliance` and `completeness` evaluators on any unscored trace, posts
the scores back, and **exits non-zero on a compliance violation** so it wires into
alerting. For subjective drift (groundedness, helpfulness) you add a Langfuse
**LLM-as-a-Judge** evaluator on Live Observations directly in the UI, sampled to
manage cost. Human sign-off happens through the **Certification Review**
annotation queue — but note that queue is populated by the *offline* certification
runner (`--queue-failures` → `cert_common.queue_failed_items`), **not** by
`monitor_production.py`; wiring live-monitored violations into it is part of the
open feedback edge ([Edge A](#closing-the-loop-what-is-wired-vs-open)).

**Langfuse primitive:** Scores on production traces + online LLM-as-a-judge +
annotation queues. **See it:** UI → **Tracing**/**Scores**, and **Annotation
Queues → Certification Review**.

## Stage 3 — Build Datasets

> *Academy:* "Turn real scenarios surfaced through monitoring and expected
> scenarios you design during development into repeatable test cases."

**This use case.** Two sources, exactly as the academy describes:
- **Designed** — the golden benchmarks: `setup_datasets.py` loads FinanceBench
  (SEC-filing QA) and FPB (sentiment) as `certification/*-sample` datasets.
- **Adversarial by design** — `sample_data/advisory_adversarial.json`: items
  crafted to tempt "guaranteed returns" language, so the compliance gate has
  something to *catch*. This is a designed edge case, not a real one.

The **real**-scenario source (a production failure becoming a dataset item) is the
open feedback edge — see [Closing the loop](#closing-the-loop-what-is-wired-vs-open).

**Langfuse primitive:** Datasets + dataset items. **See it:** UI → **Datasets**.

## Stage 4 — Experiment

> *Academy:* "Change variables systematically — a prompt, a model, a retrieval
> strategy — and compare each change against a stable baseline or other
> experimental setups."

**This use case.** `run_usecase_certification.py --use-case 10k-analyst` runs the
agent once per dataset item via Langfuse `run_experiment`, producing one comparable
run. You change **one variable** and re-run to compare:
- **Model** — `--model claude-sonnet-4-6` vs `--model claude-haiku-4-5-20251001`.
  The headline result: the *system* lifts a weaker model. Haiku alone scores ~60%
  raw numerical accuracy, but wrapped in the calculator tool the **use case** still
  PASSES at ~90% — because the arithmetic is grounded in a tool, not the model's
  head. *You are certifying the system, not the model.*
- **Prompt** — the free-form `compose`/`draft` steps fetch a `production`-labelled
  Langfuse prompt (`cert_common.get_managed_prompt`, hardcoded fallback). Edit and
  version it in the UI, promote a new version, re-run — the runs compare on the
  dashboard as distinct rows.
- **Threshold profile** — raise `numerical_accuracy` to ≥0.95 for a high-stakes
  desk; the 90%-accurate run now FAILS and the gate names the dimension.

**Langfuse primitive:** Experiments over Datasets; Prompt versions as the changed
variable. **See it:** UI → **Datasets** → run comparison; Portal → dashboard rows.

## Stage 5 — Evaluate

> *Academy:* "Decide whether results are good enough to ship using manual review,
> code evaluator checks, or LLM-as-a-judge."

**This use case.** All three evaluation methods are present, and the ship decision
is a **multi-dimensional gate** — the mechanic that earns the name *use-case*
certification. `usecase_certification_gate` (in `evaluators.py`) returns PASS
**only if every dimension clears at once**:

| Method | Evaluator | Dimension |
|---|---|---|
| Code (deterministic) | `numerical_accuracy` | correctness |
| Code (deterministic) | `regulatory_compliance` | zero prohibited phrases — **hard gate at 100%** |
| Code (deterministic) | `tool_use_correctness` | did it take the right *path* (calculator on numerical Qs)? |
| LLM-as-judge | `groundedness` | no hallucinated numbers vs the filing |
| Manual | annotation queue | human sign-off / evaluator calibration |

The clearest illustration is the **advisory-draft** agent: an answer can be
perfectly accurate and grounded and still be **uncertifiable** because it contains
one prohibited phrase — `regulatory_compliance` drops below 1.00 and the gate FAILS
regardless of the other dimensions. *Accurate ≠ shippable.* That is the whole point
of gating the use case rather than scoring the model.

**Langfuse primitive:** Scores + Score Configs + the run-level
`certification_result` gate. **See it:** the `certification_result` score comment
(per-dimension PASS/FAIL breakdown) + Portal PASS/FAIL badge.

---

## Closing the loop: what is wired vs open

The stages above all run today. What makes it a *loop* is the two edges that feed
the output of one cycle into the input of the next. Per the goal, these are
**called out honestly** — one is partial, one is not built:

### Edge A — Observation → Development (Monitor → Build Datasets) · *partially wired*

**The loop's promise:** a failure surfaced in production becomes a repeatable test
case, so you never regress on it again.

**What's wired:** `monitor_production.py` scores live traces and flags violations
(alerting via a non-zero exit). *Separately*, the **offline** certification runner
routes failing items into the **Certification Review** annotation queue
(`queue_failed_items`, via `--queue-failures`) — that path runs on `run_experiment`
results, **not** on the live traces `monitor_production.py` sees. So the two
mechanisms exist, but they are not connected to each other.

**What's open:** two links are missing. (1) `monitor_production.py` does not route a
live violation into the annotation queue — it only scores and exits. (2) More
importantly, there is **no automated path from a flagged/annotated trace to a new
golden dataset item.** Today that promotion is a manual step (copy the failing
trace's input/expected into a dataset via the UI or `setup_datasets.py`). The clean
closure — "annotate a queued trace → one click adds it to
`certification/financebench-sample` → next experiment includes it" — is a
worthwhile next build, but it is **not** implemented here. (Do not read the
annotation queue as loop-closure; it stops at human review.)

### Edge B — Ship → Re-certify (Evaluate → deploy → Trace), and the GitHub / CI-CD question · *not wired*

This is the *"is the GitHub integration part of a true CI/CD pipe?"* question, and
the answer is **yes — it is exactly the mechanism that turns prompt promotion into
governed, auditable, automatic re-certification.** Here is how it fits, and what is
missing.

**Where prompts sit in the loop.** The `compose`/`draft` steps are managed Langfuse
prompts with a `production` label. Promoting a new version *is* a deploy — it
changes production behavior with no code change. In a regulated finance use case,
an un-governed prompt edit is precisely the risk model-risk teams worry about: the
certified artifact silently drifts.

**What Langfuse's GitHub integration does** (Langfuse → GitHub, one-way; Langfuse
is the source of truth):
1. **Repository Dispatch** — a prompt-version change fires a `repository_dispatch`
   event that triggers a GitHub Actions workflow, with the payload in
   `github.event.client_payload.*`. **No extra infrastructure.**
2. **Sync to Repository** — a Prompt Version Webhook commits the prompt change into
   your repo (`"{action}: {name} v{version}"`), giving git an auditable archive of
   every prompt version. Optional `REQUIRED_LABEL` filter (e.g. only sync
   `production`). Needs a small webhook server.

**How that closes the loop into a true CI/CD pipeline for this repo:**

```
promote prompt in Langfuse UI
   │  (repository_dispatch)                    (sync-to-repo webhook)
   ▼                                                     │
GitHub Actions: run_usecase_certification.py --ci   ──┐  ▼
   │  re-runs Experiment + Evaluate on the golden set │  git commit: "updated:
   ▼                                                   │   usecase-advisory-draft v7"
gate PASS → allow the promotion to stand              │  → compliance audit trail
gate FAIL → alert / block / roll back the label ◀─────┘
```

That is the governance story: **a prompt can never be silently promoted to
production without automatically re-running use-case certification, and every
version is committed to git for audit.** Experiment + Evaluate fire on every
prompt change, not just on a code push.

**What's wired vs open here:**
- ✅ Managed prompts with `production` label + fallback (`setup_prompts.py`,
  `cert_common.get_managed_prompt`).
- ✅ A CI gate exists: `run_usecase_certification.py --ci` exits non-zero on gate
  FAIL, and `.github/workflows/certification.yml` runs certification in Actions.
- ⬜ **The trigger is wrong for CI/CD-on-prompt-change.** `certification.yml` fires
  on **push to `main`** and **manual dispatch** — *not* on a Langfuse prompt
  promotion. Wiring `repository_dispatch` (and pointing it at
  `run_usecase_certification.py --use-case … --ci`) is the missing piece that makes
  this a *true* prompt CI/CD loop.
- ⬜ **Sync-to-Repo webhook** (the git audit archive of prompt versions) is not set
  up — no webhook server, no commit-on-promote.

### Summary table

| Loop edge / stage | Langfuse primitive | Status in this repo |
|---|---|---|
| 1 Trace | Observations (nested spans) | ✅ wired — all 3 agents |
| 2 Monitor | Scores on live traces + online judge + queues | ✅ wired (`monitor_production.py`) |
| 3 Build Datasets | Datasets + items | ✅ wired (designed + adversarial); real-failure source open (Edge A) |
| 4 Experiment | Experiments; prompt versions | ✅ wired (model / prompt / threshold comparisons) |
| 5 Evaluate | Scores + multi-dim gate | ✅ wired (`usecase_certification_gate`) |
| **A** Monitor → Dataset | queue → dataset promotion | 🟡 partial — human queue yes, auto-promotion no |
| **B** Prompt promote → re-cert | GitHub `repository_dispatch` + sync webhook | ⬜ not wired — substrate present, trigger missing |

---

## Demoing this

`main` carries the **assembled** state: the shared foundation plus all three
agents (#9 10k-analyst, #10 sentiment-triage, #11 advisory-draft) plus the
offline-test CI, verified together (`run_usecase_certification.py --list` shows
all three `[registered]`; 52 offline tests pass). Run the full narrative end to
end from `main`.

The 5-minute live walk-through is scripted in the
[runbook](usecase-runbook.md#6-demo-script-the-5-minute-story). Loop-shaped
narration:

1. **Trace** — open a 10‑K item; walk plan → retrieve → **calculate** → compose.
2. **Evaluate** — read the `certification_result` breakdown; PASS = all dimensions.
3. **Experiment** — re-run on Haiku; the *system* still PASSES (calculator lift).
4. **Evaluate (the FAIL)** — advisory-draft on the adversarial item: perfectly
   accurate, but one prohibited phrase → compliance < 100% → **gate FAILS**.
   *Accurate but uncertifiable.*
5. **Monitor + close the loop** — point at `monitor_production.py` on live traffic,
   then name the two open edges above as the "make it continuous / make it CI/CD"
   next steps.

## How this doc relates to the others

| Doc | Answers |
|---|---|
| **this** (`ai-engineering-loop.md`) | *What is the objective, and how does the whole thing form a loop?* |
| [`usecase-architecture.md`](usecase-architecture.md) | *What are the components and where does each live?* |
| [`usecase-certification.md`](usecase-certification.md) | *How is each agent implemented?* |
| [`usecase-runbook.md`](usecase-runbook.md) | *How do I run and demo it?* |
