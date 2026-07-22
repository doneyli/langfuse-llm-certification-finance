# Agent Deployment Gate — Architecture & Eval Lifecycle

Companion to [`ai-engineering-loop.md`](ai-engineering-loop.md) (the objective /
loop story), [`usecase-certification.md`](usecase-certification.md) (the
implementation spec) and [`usecase-runbook.md`](usecase-runbook.md) (how to run
it). This document shows the architecture and maps every stage of the evaluation
lifecycle to the Langfuse primitive and the file that owns it. The foundation
(PR #12, issue #8) and all three agents — #9 (PR #13), #10 (PR #14), #11 (PR #15) —
are now merged to `main`.

> **Note on naming.** "Certification" is the internal name for this process — the
> workstream, scripts, and dataset prefixes keep it. Nothing here issues a
> certificate: the pipeline is a **deployment gate** that produces reviewable
> evidence, and sign-off stays with the people accountable for the deployment.

---

## 1. System architecture

```
                         ┌───────────────────────────────────────────────┐
                         │                  LANGFUSE                       │
                         │                                                 │
  setup_datasets.py ───▶ │  Datasets ──────┐                               │
  setup_prompts.py  ───▶ │  Prompts        │                               │
  setup_score_configs──▶ │  Score Configs  │                               │
  setup_annotation_q. ─▶ │  Annotation Q.  │                               │
                         └─────────┬────────┼──────────────────────────────┘
                                   │        │ get_dataset / get_prompt
                                   │        ▼
                      ┌────────────┴───────────────────────────────────┐
                      │      run_usecase_certification.py (runner)      │
                      │  • dispatch on AGENT_REGISTRY[use_case]         │
                      │  • build item evaluators + multi-dim gate       │
                      │  • metadata.model = "usecase:<name>"            │
                      └────────────┬───────────────────────────────────┘
                                   │ dataset.run_experiment(task=agent)
                                   ▼
        ┌──────────────────────────────────────────────────────────────┐
        │                AGENT  (agents/<use_case>.py)                   │
        │   one nested trace per dataset item:                           │
        │                                                                │
        │     plan (generation) ─▶ retrieve (span) ─▶ calculate (tool)   │
        │                                       └────▶ compose (gen)     │
        │   returns AgentResult{answer, trajectory}                      │
        └────────────┬─────────────────────────────────────────────────┘
                     │ spans + output
                     ▼
        ┌──────────────────────────────────────────────────────────────┐
        │  EVALUATORS (evaluators.py)                                    │
        │   item:  numerical_accuracy, groundedness (judge),             │
        │          regulatory_compliance, tool_use_correctness           │
        │   run:   avg_<dim> × N  +  usecase_certification_gate          │
        │            └─▶ certification_result = PASS iff ALL dims clear   │
        └────────────┬─────────────────────────────────────────────────┘
                     │ persist_run_evaluations (scores) ; queue_failed_items
                     ▼
   ┌──────────────────────────┐     ┌───────────────────────────────────────┐
   │  Portal (FastAPI + React)│     │  Langfuse UI                          │
   │  row: usecase:<name>     │     │  Traces · Scores · Annotation Queues  │
   │  PASS/FAIL badge         │     │  Dashboards · Prompts                 │
   └──────────────────────────┘     └───────────────────────────────────────┘
                     ▲
                     │ continuous, post-deployment
        ┌────────────┴───────────────┐
        │  monitor_production.py      │  compliance + completeness on live traces
        └─────────────────────────────┘
```

The **only** structural differences from the model deployment gate: the `task` is a
multi-span agent instead of one call, and the run-level gate is multi-dimensional —
every dimension must pass. Everything else (datasets, prompts, scores, queues,
portal, monitoring) is the shared pipeline.

---

## 2. The trace an agent emits under the gate

One trace per dataset item; the gate aggregates across items. Example —
10-K Analyst on a numerical-reasoning question:

```
usecase:10k-analyst                                   trace (run_experiment item)
│  input:  {question, evidence[], company}
│  output: {answer, trajectory{steps, tools_used, operands, citations}}
│
├── plan                 generation   model=claude-…  in:question+qtype  out:"CALCULATE: revenue / avg PP&E"
├── retrieve-evidence    span                          out:{operands:{revenue:6489, ppe_2019:253, ppe_2018:282}}
├── calculate            tool                           in:"6489/((253+282)/2)"  out:24.26
└── compose-answer       generation   model=claude-…  out:"The FY2019 fixed asset turnover is 24.26 …"
│
└─ scores (item):  numerical_accuracy=1.0  groundedness=0.92
                   regulatory_compliance=1.0  tool_use_correctness=1.0
```

Run-level scores attached to the first trace: `avg_numerical_accuracy`,
`avg_groundedness`, `avg_regulatory_compliance`, `avg_tool_use_correctness`,
`certification_result` (1.0 PASS / 0.0 FAIL with a per-dimension breakdown comment).

---

## 3. Eval lifecycle component map

Every stage of the evaluation lifecycle, the Langfuse primitive that backs it, the
file that owns it, and its status in this workstream.

| # | Lifecycle stage | Langfuse primitive | Owned by | Status |
|---|---|---|---|---|
| 1 | **Golden data** | Datasets | `setup_datasets.py` | ✅ wired (reused unchanged) |
| 2 | **Prompt management** | Prompts (versioned, `production` label) | `setup_prompts.py` (templates) + `cert_common.get_managed_prompt` (fetch) | ✅ templates registered; agents consume in #9–#11 |
| 3 | **Agent execution & tracing** | Observations: `generation` / `span` / `tool` | `agents/base.py` (`traced_generation/span/tool`) + each agent | ✅ wired; span-nesting inside `run_experiment` **verified** (spike + live runs); all three agents implemented — #9 (PR #13), #10 (PR #14), #11 (PR #15) |
| 4 | **Item scoring** | Scores + Score Configs | `evaluators.py` + `setup_score_configs.py` | ✅ wired (incl. new `tool_use_correctness`) |
| 5 | **Run-level gate** | Score `certification_result` | `evaluators.usecase_certification_gate` + `cert_common.persist_run_evaluations` | ✅ wired (multi-dimensional, all-must-pass) |
| 6 | **Human review** | Annotation Queues | `setup_annotation_queues.py` + `cert_common.queue_failed_items` | ✅ wired (reused) |
| 7 | **Reporting / status** | Portal + export | Portal (`metadata.model="usecase:<name>"`) + `export_results.py` | ✅ free dashboard row, no portal change |
| 8 | **Production monitoring** | Online evaluation / live traces | `monitor_production.py` + UI LLM-as-judge | ✅ reused; applies to deployed agents |

Legend: ✅ wired and verified · 🟡 partially wired, has a live-unverified assumption ·
⬜ pending. (As of the agent PRs #13/#14/#15, every stage is ✅.)

---

## 4. Two deployment-gate modes, one pipeline

| | Model deployment gate | Agent deployment gate |
|---|---|---|
| Entry point | `run_certification.py` | `run_usecase_certification.py` |
| `task` | single LLM call (`create_certification_task`) | agent factory from `AGENT_REGISTRY` |
| Trace | one flat generation | nested tree (plan→retrieve→tool→compose) |
| Item evaluators | accuracy / sentiment / compliance / groundedness | + `tool_use_correctness` |
| Run gate | `certification_gate(score, threshold)` | `usecase_certification_gate({dim: thr, …})` |
| Dashboard `model` | the model name | `usecase:<name>` |
| Shared | datasets · prompts · score configs · annotation queues · portal · monitoring · `cert_common` plumbing | ← identical |

The shared `cert_common.py` is what keeps the two runners from drifting:
credentials, prompt fetch, score persistence, and queue routing live in one place.

---

## 5. Where the risk was

- **Span nesting (stage 3) — RESOLVED.** The open question was whether observations
  opened *inside a `run_experiment` task callback* nest under the per-item trace or
  become separate root traces. Confirmed nesting via the spike
  (`scripts/spike_span_nesting.py`, #9) and every agent's live run since: the
  10-K Analyst, Sentiment Triage, and Advisory Draft agents all render a single
  nested trace per item (plan/classify/analyze → … → tool), so no explicit
  `trace_context` threading is needed.
- **Prompt drift.** Agent prompts are registered but unversioned-in-anger until an
  agent actually fetches them; once #9 runs, promote/rollback works exactly like
  `financial-qa`.
- Everything else reuses already-proven pipeline components.
