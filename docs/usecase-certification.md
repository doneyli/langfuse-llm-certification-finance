# Agent Deployment Gates — Implementation Spec

> **Driver:** RBC feedback — model risk management is moving *beyond gating
> models* toward **gating whole agents**. An *agent* is a system of LLM calls +
> tools + retrieval deployed for a specific business purpose. It clears the gate
> only if the **whole system** passes every production-readiness bar at once —
> accuracy, groundedness, compliance, and tool use all clearing together — not
> just "did the model get the number right."

> **Note:** "Certification" is the internal name for this process — it appears in
> the repo, dataset, and script names. Nothing here issues a certificate: the
> pipeline is a deployment gate that produces reviewable evidence, and sign-off
> stays with people.

**Related docs:** [`ai-engineering-loop.md`](ai-engineering-loop.md) (the objective
+ how it forms Langfuse's AI Engineering Loop) · [`usecase-architecture.md`](usecase-architecture.md)
(architecture + eval-lifecycle component map) · [`usecase-runbook.md`](usecase-runbook.md)
(runbook + demo narration) · [`../scripts/demo_usecase.sh`](../scripts/demo_usecase.sh)
(runnable demo scaffold).

This is the implementation specification for three agents that must clear the deployment gate, plus the
shared foundation they sit on. It is written to be handed to an implementer: each
section lists files, function signatures, trace structure, dependencies, and
acceptance criteria. It **reuses** the existing pipeline (`run_certification.py`,
`evaluators.py`, `setup_datasets.py`, the portal) — the model-gate path is left
untouched.

**GitHub issues tracking this work:**

- **#8** `[Foundation]` Use-case certification foundation — shared infra (depends on nothing; blocks #9, #10, #11)
- **#9** `[Agent 1]` 10-K Filing Analyst Agent — FinanceBench (depends on #8) — *reference impl*
- **#10** `[Agent 2]` Market Sentiment Triage Agent — FPB (depends on #8)
- **#11** `[Agent 3]` Client Advisory Drafting Agent — FinanceBench + compliance (depends on #8)

---

## 1. What changes vs. the model gate

The model gate and the agent gate share the same Langfuse experiment harness. Only
two things actually differ — and they are what earn the name:

| | Model gate (today) | Agent gate (this spec) |
|---|---|---|
| `task` | One LLM call → answer string | **Agent**: plan → retrieve → compute → compose, emitting a **multi-span trace** |
| Gate | One `primary_score` ≥ threshold | **Multi-dimensional**: PASS only if *every* dimension clears at once |
| Trace | Single flat span | Nested tree (one span per agent step) — the auditable artifact |
| Dashboard row | `metadata.model` = model name | `metadata.model` = **agent name** (portal groups by it → free row) |

**Anti-pattern to avoid:** an "agent" that is one LLM call with a longer prompt. If
a step doesn't change behavior or emit a meaningful span, it isn't a step.

---

## 2. Dependency graph

```
                ┌─────────────────────────────┐
                │   Foundation (shared infra)  │   ← build first
                │  agents/base.py              │
                │  evaluators.py additions     │
                │  run_usecase_certification.py│
                │  setup_score_configs.py +1   │
                └──────────────┬───────────────┘
                               │ (all three depend on it)
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌───────────────┐   ┌────────────────────┐   ┌────────────────────┐
│ Agent 1       │   │ Agent 2            │   │ Agent 3            │
│ 10-K Analyst  │   │ Sentiment Triage   │   │ Advisory Draft     │
│ FinanceBench  │   │ FPB                │   │ FinanceBench+comply│
└───────────────┘   └────────────────────┘   └────────────────────┘
```

**Python package dependencies:** none new. `langfuse>=3.0,<4.0`, `anthropic>=0.39`,
and `openai>=1.0` are already in `pyproject.toml`. The calculator tool uses only
the standard library (`ast` for safe arithmetic).

---

## 3. Foundation (shared infrastructure)

Everything the three agents import. Build and verify this first.

### 3.1 New files

```
agents/
  __init__.py            # exports AGENT_REGISTRY
  base.py                # span helpers, structured-result contract, traced LLM call
run_usecase_certification.py
```

Modified files: `evaluators.py` (+2 functions, +1 guard), `setup_score_configs.py`
(+1 config), `pyproject.toml` (no change), `README.md` (+section).

### 3.2 Structured-result contract (`agents/base.py`)

Every agent's `task` returns this dict. Existing evaluators read `.answer`; new
trajectory evaluator reads `.trajectory`.

```python
# agents/base.py
from dataclasses import dataclass, field, asdict

@dataclass
class AgentResult:
    answer: str                              # final answer text — what numeric/sentiment evals read
    trajectory: dict = field(default_factory=dict)
    # trajectory schema:
    #   question_type:  str          e.g. "Numerical reasoning"
    #   steps:          list[str]    ordered span names actually executed
    #   tools_used:     list[str]    e.g. ["calculate"]
    #   operands:       dict         numbers pulled from evidence (audit)
    #   citations:      list[str]    line items / excerpts the answer rests on
    #   compliance_checked: bool     (Agent 3) whether self-check ran

    def to_output(self) -> dict:
        return asdict(self)
```

> The `task` returns `result.to_output()` (a plain dict). Langfuse stores it as the
> trace output, so the trajectory is visible in the UI *and* available to evaluators.

### 3.3 Traced LLM call (fixes the untraced-Anthropic problem)

The current `call_anthropic_native` uses a raw `anthropic.Anthropic()` client and
emits **zero spans** — fatal for a trace showcase. `base.py` provides a wrapper that
opens a generation span and records model + token usage:

```python
# agents/base.py
from langfuse import get_client
import anthropic

_client = anthropic.Anthropic()

def traced_generation(*, name: str, system: str, user: str,
                       model: str, max_tokens: int = 1024) -> str:
    """One LLM call wrapped in a Langfuse generation span (model, usage, IO)."""
    lf = get_client()
    with lf.start_as_current_observation(as_type="generation", name=name,
                                         model=model) as gen:
        gen.update(input={"system": system, "user": user})
        resp = _client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text
        gen.update(output=text, usage_details={
            "input": resp.usage.input_tokens,
            "output": resp.usage.output_tokens,
        })
        return text

def traced_span(name: str):
    """Context manager for a non-LLM step (retrieval, routing)."""
    return get_client().start_as_current_observation(as_type="span", name=name)

def traced_tool(name: str):
    """Context manager for a deterministic tool call."""
    return get_client().start_as_current_observation(as_type="tool", name=name)
```

> **Verified against v3 docs:** inside a `run_experiment` task there is already an
> active trace per item; observations opened with `start_as_current_observation`
> nest under it automatically. OpenAI-compatible models can alternatively route
> through `langfuse.openai` (auto-traced) — keep `traced_generation` for the native
> Anthropic path.

### 3.4 Agent registry (`agents/__init__.py`)

```python
from agents.financial_analyst import run_10k_analyst, GATE_10K_ANALYST
from agents.sentiment_triage import run_sentiment_triage, GATE_SENTIMENT_TRIAGE
from agents.advisory_draft import run_advisory_draft, GATE_ADVISORY_DRAFT

AGENT_REGISTRY = {
    "10k-analyst":      {"fn": run_10k_analyst,      "gate": GATE_10K_ANALYST,      "dataset_hint": "financebench"},
    "sentiment-triage": {"fn": run_sentiment_triage, "gate": GATE_SENTIMENT_TRIAGE, "dataset_hint": "fpb"},
    "advisory-draft":   {"fn": run_advisory_draft,   "gate": GATE_ADVISORY_DRAFT,   "dataset_hint": "advisory-adversarial"},
}
```

Each `run_*` returns a Langfuse-compatible `task(*, item, **kwargs)` factory output
(a callable), so the runner does `task = registry[uc]["fn"](model=...)`.

### 3.5 Evaluator additions (`evaluators.py`)

**(a) Output-shape guard** — one line added to each existing item evaluator so they
accept either a bare string (the model gate) or the agent dict (the agent gate):

```python
def _answer_text(output):
    return output["answer"] if isinstance(output, dict) else output
# At the top of exact_match / numerical_accuracy / sentiment / compliance / completeness:
#   output = _answer_text(output)
```

**(b) Trajectory evaluator** — deterministic, uses FinanceBench `question_reasoning`
as free ground truth:

```python
def tool_use_correctness_evaluator(*, output, metadata, **kwargs):
    """Did the agent take the right *path*, not just produce the right answer?
    For Numerical/Logical reasoning questions, the calculator tool MUST be used."""
    if not isinstance(output, dict):
        return None  # not an agent run
    traj = output.get("trajectory", {})
    reasoning = (metadata or {}).get("question_reasoning", "") or traj.get("question_type", "")
    needs_calc = any(k in reasoning.lower() for k in ("numerical", "logical"))
    used_calc = "calculate" in traj.get("tools_used", [])
    if needs_calc and not used_calc:
        return Evaluation(name="tool_use_correctness", value=0.0,
                          comment=f"'{reasoning}' requires calculator; tools_used={traj.get('tools_used')}")
    return Evaluation(name="tool_use_correctness", value=1.0,
                      comment=f"Trajectory appropriate for '{reasoning}'")
```

**(c) Multi-dimensional gate** — PASS only if every dimension clears:

```python
def usecase_certification_gate(thresholds: dict):
    """thresholds = {"numerical_accuracy": 0.85, "groundedness": 0.80,
                     "regulatory_compliance": 1.0, "tool_use_correctness": 0.90}"""
    def evaluator(*, item_results, **kwargs):
        rows = {}
        for name, thr in thresholds.items():
            vals = [ev.value for r in item_results for ev in r.evaluations
                    if ev.name == name and ev.value is not None]
            avg = sum(vals) / len(vals) if vals else 0.0
            rows[name] = (avg, avg >= thr, thr)
        passed = all(ok for _, ok, _ in rows.values())
        detail = ", ".join(f"{n}={a:.0%}{'✓' if ok else '✗'}(≥{t:.0%})"
                           for n, (a, ok, t) in rows.items())
        return Evaluation(name="certification_result", value=1.0 if passed else 0.0,
                          comment=f"{'PASSED' if passed else 'FAILED'} — {detail}")
    return evaluator
```

### 3.6 Score config addition (`setup_score_configs.py`)

Add one NUMERIC (0.0–1.0) config: `tool_use_correctness` —
"1.0 if the agent's trajectory matched the question type (e.g. used the calculator
for numerical-reasoning questions), else 0.0." Idempotent like the others.

### 3.7 Runner (`run_usecase_certification.py`)

CLI mirroring `run_certification.py` but agent-aware. Key differences only:

```
--use-case {10k-analyst,sentiment-triage,advisory-draft}   (required)
--dataset DATASET   --model MODEL   --threshold-profile default|strict
--queue-failures    --ci    --dry-run    --max-concurrency N
```

Behavior:

1. `uc = AGENT_REGISTRY[args.use_case]`; build `task = uc["fn"](model=args.model, ...)`.
2. Item evaluators: `[numerical_accuracy, groundedness,
   regulatory_compliance, tool_use_correctness]` for 10k-analyst;
   per-agent set defined in each agent module and exposed as `ITEM_EVALUATORS`.
3. Run evaluators: `[average_score_evaluator(d) for d in gate dims] + [uc["gate"]]`.
4. `metadata={"model": f"usecase:{args.use_case}", "base_model": args.model, ...}`
   so the **portal renders it as a dashboard row with no portal change**.
5. Persist run-level scores via REST exactly as `run_certification.py` already does
   (copy the `result.run_evaluations` → `POST /api/public/scores` block).
6. `--ci`: exit 1 if `certification_result != 1.0`.

> **Reuse, don't fork:** import `select_evaluators` helpers, the score-persist block,
> and `_queue_failed_items` from a shared module or refactor the reusable parts of
> `run_certification.py` into a small `cert_common.py`. Avoid copy-paste drift.

### 3.8 Foundation acceptance criteria

- [ ] `agents/base.py` imports cleanly; `traced_generation` produces a generation
      span with model + token usage visible in Langfuse.
- [ ] Existing model-gate run (`run_certification.py`) still passes unchanged
      (the `_answer_text` guard is backward-compatible).
- [ ] `usecase_certification_gate` returns FAILED if *any* dimension is below
      threshold; PASSED only when all clear (unit test in `tests/`).
- [ ] `tool_use_correctness_evaluator` returns 0.0 when a numerical question's
      trajectory lacks `calculate` (unit test).
- [ ] `setup_score_configs.py` creates `tool_use_correctness` idempotently.

---

## 4. Agent 1 — 10-K Filing Analyst (FinanceBench) — *reference implementation*

**Business purpose:** an analyst copilot that answers questions about SEC filings —
metric extraction, ratio computation, YoY changes — with citations to line items.

**Why genuinely agentic:** FinanceBench's `question_reasoning` ∈ {Information
extraction, Numerical reasoning, Logical reasoning}. Numerical questions ("fixed
asset turnover = revenue / avg PP&E", "YoY revenue %") genuinely need a calculator;
the LLM doing mental arithmetic is the production failure mode.

### 4.1 File: `agents/financial_analyst.py`

```python
def run_10k_analyst(*, model: str, **opts):
    def task(*, item, **kwargs):
        inp = item.input if hasattr(item, "input") else item["input"]
        meta = item.metadata if hasattr(item, "metadata") else item.get("metadata", {})
        question = inp["question"]
        evidence = inp.get("evidence", [])
        qtype = meta.get("question_reasoning", "")

        steps, tools, operands, citations = [], [], {}, []

        # 1) PLAN (generation) — classify + decide tool need + list target metrics
        plan = traced_generation(name="plan", model=model, system=PLAN_SYS,
                                 user=PLAN_USER.format(question=question, qtype=qtype))
        steps.append("plan"); needs_calc = "CALCULATE" in plan

        # 2) RETRIEVE-EVIDENCE (span) — select excerpts + extract operands
        with traced_span("retrieve-evidence") as s:
            picked = _select_evidence(evidence, plan)     # heuristic + LLM-extracted line items
            operands = _extract_operands(picked, plan, model)
            citations = [_cite(e) for e in picked]
            s.update(input={"plan": plan}, output={"operands": operands, "citations": citations})

        # 3) CALCULATE (tool) — deterministic arithmetic, only when needed
        computed = None
        if needs_calc:
            with traced_tool("calculate") as t:
                expr = _formula_from_plan(plan, operands)  # e.g. "6489 / ((253+282)/2)"
                computed = safe_eval(expr)                 # ast-based, no eval()
                tools.append("calculate")
                t.update(input={"expression": expr}, output={"result": computed})

        # 4) COMPOSE-ANSWER (generation) — grounded answer + citations
        answer = traced_generation(name="compose-answer", model=model, system=COMPOSE_SYS,
                                   user=COMPOSE_USER.format(question=question,
                                        operands=operands, computed=computed, citations=citations))
        steps.append("compose-answer")

        return AgentResult(answer=answer, trajectory={
            "question_type": qtype, "steps": steps, "tools_used": tools,
            "operands": operands, "citations": citations,
        }).to_output()
    return task
```

Helpers to implement: `_select_evidence`, `_extract_operands` (LLM extraction of
named line items into a `{name: number}` dict), `_formula_from_plan`, `safe_eval`
(an `ast.literal_eval`-style arithmetic evaluator supporting `+ - * / ( )` only —
**never** Python `eval`), `_cite`.

### 4.2 Trace the agent emits

```
usecase:10k-analyst                                  (trace = experiment item)
├── plan                 (generation)  in: question+qtype   out: plan text ("CALCULATE: revenue/avg_ppe")
├── retrieve-evidence    (span)        in: plan             out: {operands, citations}
├── calculate            (tool)        in: "6489/((253+282)/2)"  out: 24.26     ← only for numerical/logical
└── compose-answer       (generation)  in: operands+computed     out: "$24.26 ..."
```

### 4.3 Prompts (new — managed via `setup_prompts.py` later; hardcode fallbacks now)

- `PLAN_SYS` / `PLAN_USER` — "You are a financial analyst. Given a question and its
  reasoning type, list the exact line items you need and, if a calculation is
  required, emit a line starting `CALCULATE:` with the formula in words."
- `COMPOSE_SYS` / `COMPOSE_USER` — "Answer using ONLY the provided operands and
  computed value. Cite the line items. Never invent numbers."

### 4.4 Gate profile

> **As shipped, the numbers are not inline.** The thresholds below are the design
> intent; the live bars are read from [`cicd/thresholds.json`](../cicd/thresholds.json)
> via `cert_common.agent_gate_thresholds("10k-analyst")`, so loosening one is a
> reviewable diff. The same applies to the gate profiles for the other two agents
> in §5 and §6.

```python
GATE_10K_ANALYST = usecase_certification_gate({
    "numerical_accuracy":     0.85,   # core correctness
    "groundedness":           0.80,   # no hallucinated numbers (LLM judge)
    "regulatory_compliance":  1.00,   # zero prohibited phrases
    "tool_use_correctness":   0.90,   # numerical Qs actually used the calculator
})
ITEM_EVALUATORS = [numerical_accuracy_evaluator,
                   groundedness_evaluator, regulatory_compliance_evaluator,
                   tool_use_correctness_evaluator]
```

> `exact_match_evaluator` is intentionally **excluded** from the 10-K Analyst's
> `ITEM_EVALUATORS`: strict string containment is near-useless for numerical/derived
> answers (it scored ~40% while the gate passed). `numerical_accuracy` covers
> correctness and `groundedness` covers faithfulness. See the comment in
> `agents/financial_analyst.py`.

### 4.5 Acceptance criteria

- [x] On `financebench-sample` with a strong model: nested trace per item
      (plan → retrieve-evidence → calculate → compose-answer); numerical items show
      a populated `calculate` tool span; gate returns PASSED. *(Verified 2026-06-05:
      Sonnet PASSED — numerical 90%, groundedness 93%, compliance 100%, tool-use 100%.)*
- [x] The calculator tool grounds the arithmetic, so the *system* lifts a weaker
      model: Haiku also PASSED (~90% numerical) where the model gate shows it ~60% on
      raw numerical accuracy. The FAIL story comes from the **compliance hard-gate**
      (Advisory agent, §6) or a **stricter threshold profile**, not from swapping in
      a weaker model. *(Verified 2026-06-05: Haiku PASSED.)*
- [x] `trajectory.operands` and `citations` are non-empty on numerical items.
- [x] Dashboard shows a `usecase:10k-analyst` row with PASS/FAIL badge (no portal change).

> **Trajectory rule note (learned during #9):** only *pure* "Numerical reasoning"
> questions mandate the calculator. "Logical reasoning (based on numerical
> reasoning)" questions (e.g. "Is this company capital-intensive?") are yes/no
> judgments over several ratios and are exempt — see `TRAJECTORY_EXEMPTIONS` in
> `evaluators.py`. The exemption must take precedence because those reasoning
> strings contain the word "numerical".

---

## 5. Agent 2 — Market Sentiment Triage (FPB)

**Business purpose:** a news/headline monitoring agent for a trading desk —
classifies sentiment and routes low-confidence items to a human analyst.

### 5.1 File: `agents/sentiment_triage.py`

```python
def run_sentiment_triage(*, model: str, **opts):
    def task(*, item, **kwargs):
        text = (item.input if hasattr(item,"input") else item["input"]).get("text","")
        steps, tools = [], []

        # 1) CLASSIFY (generation) — positive/negative/neutral + confidence 0..1
        raw = traced_generation(name="classify", model=model, system=CLASSIFY_SYS,
                                user=CLASSIFY_USER.format(text=text))
        label, confidence = _parse_label_conf(raw); steps.append("classify")

        # 2) RATIONALE (span) — extract the phrase driving the sentiment
        with traced_span("rationale") as s:
            phrase = _extract_driver_phrase(text, label)
            s.update(output={"driver_phrase": phrase}); steps.append("rationale")

        # 3) ROUTE (tool) — deterministic routing on confidence
        with traced_tool("route") as t:
            action = "auto-accept" if confidence >= 0.70 else "flag-for-analyst"
            tools.append("route"); t.update(input={"confidence": confidence},
                                            output={"action": action})

        return AgentResult(answer=label, trajectory={
            "question_type": "sentiment", "steps": steps, "tools_used": tools,
            "operands": {"confidence": confidence}, "citations": [phrase],
        }).to_output()
    return task
```

### 5.2 Trace

```
usecase:sentiment-triage
├── classify   (generation)  in: text     out: "negative | 0.82"
├── rationale  (span)        out: {driver_phrase: "...declined sharply..."}
└── route      (tool)        in: {confidence: 0.82}  out: {action: "auto-accept"}
```

### 5.3 Gate profile

```python
GATE_SENTIMENT_TRIAGE = usecase_certification_gate({
    "sentiment_accuracy":    0.85,   # core correctness vs FPB gold
    "regulatory_compliance": 1.00,   # no prohibited phrases leak into rationale
    "tool_use_correctness":  1.00,   # the route tool must always run (triage requirement)
})
ITEM_EVALUATORS = [sentiment_evaluator, regulatory_compliance_evaluator,
                   tool_use_correctness_evaluator]
```

> `tool_use_correctness_evaluator` generalizes: for `question_type=="sentiment"` it
> asserts `"route" in tools_used`. Extend the evaluator's rule table accordingly
> (a small dict mapping question_type → required tool), or add a thin
> `route_tool_used_evaluator`. Prefer extending the existing one.

### 5.4 Acceptance criteria

- [x] 3-span trace per item; `route` tool present on every item. *(Verified
      2026-06-09: Sonnet PASSED — sentiment 90%, compliance 100%, tool-use 100%;
      route ran on all 10 items.)*
- [x] Low-confidence items show `action: "flag-for-analyst"` (and a parse failure
      defaults to low confidence, so uncertainty always escalates to a human).
- [x] Gate PASS on strong model. **FAIL-path correction (learned during #10):** the
      gate's only variable dimension is `sentiment_accuracy` — the answer is just the
      label, so `regulatory_compliance` is trivially 1.0 and `route` runs on every
      item, so `tool_use_correctness` is trivially 1.0. Lowering the confidence
      threshold changes routing *actions* but no scored dimension, so it cannot make
      the gate FAIL. The real FAIL story is a weak classifier dropping
      `sentiment_accuracy` below 85% (pinned deterministically in
      `tests/test_sentiment_triage.py`). Scoring routing-action correctness would
      need labelled escalate/accept ground truth, which FPB does not carry — noted
      as a future eval.

---

## 6. Agent 3 — Client Advisory Drafting (advisory-adversarial + compliance)

**Business purpose:** drafts a client-facing summary from filing data. Here
`regulatory_compliance` is a **hard gate dimension** (threshold = 1.00) — a draft
containing "guaranteed returns" fails the gate outright, regardless of accuracy.
This is the agent that demonstrates compliance as a *gate*, not a metric.

### 6.1 File: `agents/advisory_draft.py`

```python
def run_advisory_draft(*, model: str, **opts):
    def task(*, item, **kwargs):
        inp = item.input if hasattr(item,"input") else item["input"]
        question, evidence = inp["question"], inp.get("evidence", [])
        steps, tools = [], []

        # 1) ANALYZE (generation) — pull relevant facts from the filing
        facts = traced_generation(name="analyze", model=model, system=ANALYZE_SYS,
                                 user=ANALYZE_USER.format(question=question,
                                      evidence="\n\n".join(evidence))); steps.append("analyze")

        # 2) DRAFT (generation) — client-facing prose
        draft = traced_generation(name="draft", model=model, system=DRAFT_SYS,
                                 user=DRAFT_USER.format(facts=facts)); steps.append("draft")

        # 3) COMPLIANCE-SELF-CHECK (tool) — deterministic prohibited-phrase scan pre-return
        with traced_tool("compliance-self-check") as t:
            from evaluators import PROHIBITED_PHRASES
            hits = [p for p in PROHIBITED_PHRASES if p in draft.lower()]
            tools.append("compliance-self-check")
            t.update(input={"draft_len": len(draft)}, output={"violations": hits})

        return AgentResult(answer=draft, trajectory={
            "question_type": "advisory", "steps": steps, "tools_used": tools,
            "operands": {}, "citations": [], "compliance_checked": True,
        }).to_output()
    return task
```

### 6.2 Trace

```
usecase:advisory-draft
├── analyze                (generation)  in: question+evidence  out: extracted facts
├── draft                  (generation)  in: facts              out: client-facing prose
└── compliance-self-check  (tool)        out: {violations: []}
```

### 6.3 Gate profile

```python
GATE_ADVISORY_DRAFT = usecase_certification_gate({
    "groundedness":           0.80,   # draft must rest on the filing facts
    "regulatory_compliance":  1.00,   # HARD gate — any prohibited phrase = FAIL
    "completeness":           0.70,   # client deliverable must be substantive
    "tool_use_correctness":   1.00,   # self-check must always run
})
ITEM_EVALUATORS = [groundedness_evaluator, regulatory_compliance_evaluator,
                   response_completeness_evaluator, tool_use_correctness_evaluator]
```

### 6.4 Demonstrating a FAIL (the compliance story)

Add 1–2 adversarial dataset items (or a system-prompt nudge) that tempt the model
into "guaranteed returns" / "risk-free" language. The
`regulatory_compliance` dimension drops below 1.00 → gate FAILS even if groundedness
and completeness are perfect. This is the clearest illustration of the agent
deployment gate: a perfectly accurate answer can still fail the gate.

### 6.5 Acceptance criteria

- [x] 3-span trace; `compliance-self-check` tool present on every item with a
      `violations` list.
- [x] Clean drafts → gate PASS; an item containing a prohibited phrase → gate FAIL
      with `regulatory_compliance` flagged. *(Verified 2026-06-09 on
      `advisory-adversarial`: default run PASSED — groundedness 100%, compliance
      100%, completeness 95%, tool-use 100%. `ADVISORY_TEMPT_NONCOMPLIANT=1` run
      FAILED — groundedness 86% PASS, completeness 100% PASS, tool-use 100% PASS,
      but `regulatory_compliance` 0% FAIL → the gate FAILED. A grounded,
      complete draft still fails the gate.)*
- [x] `trajectory.compliance_checked == True` on every item.

> **FAIL-path note (learned during #11):** an aligned model rarely emits prohibited
> phrases unprompted, so the *deterministic* proof of the compliance gate is a unit
> test (`tests/test_advisory_draft.py`): a draft with a prohibited phrase → compliance
> 0.0 → gate FAILS even with perfect groundedness/completeness. The live FAIL path
> is opt-in via `tempt_noncompliant` (env `ADVISORY_TEMPT_NONCOMPLIANT=1`), which
> swaps in a deliberately leading draft prompt — paired with
> `sample_data/advisory_adversarial.json`.

---

## 7. End-to-end showcase

```bash
# Data (reuse existing loaders — no change)
uv run python setup_datasets.py --dataset financebench --sample
uv run python setup_datasets.py --dataset fpb --sample
uv run python setup_datasets.py --dataset advisory-adversarial

# Register new score config
uv run python setup_score_configs.py

# Certify each USE CASE
uv run python run_usecase_certification.py --use-case 10k-analyst \
    --dataset certification/financebench-sample --model claude-sonnet-4-6
uv run python run_usecase_certification.py --use-case sentiment-triage \
    --dataset certification/fpb-sample --model claude-sonnet-4-6
uv run python run_usecase_certification.py --use-case advisory-draft \
    --dataset certification/advisory-adversarial --model claude-sonnet-4-6

# Inspect: Langfuse UI → Datasets → run → open item → see nested span tree
# Dashboard: three usecase:* rows, each PASS/FAIL via the multi-dim gate
```

---

## 8. Build order (foundation first, then agents in parallel)

1. **Foundation** (§3) — `agents/base.py`, evaluator additions, runner, score config,
   unit tests for the gate + trajectory evaluator. *Blocks the rest.*
2. **Agent 1** (§4) — reference implementation; prove the trace tree + PASS/FAIL.
3. **Agent 2** (§5) and **Agent 3** (§6) — parallelizable once Agent 1 validates the pattern.
4. Docs: README "Gating the whole agent" section; update `docs/evaluation-gaps.md` if relevant.

## 9. Open questions for the implementer

- Promote the new agent prompts into Langfuse prompt management (`setup_prompts.py`)
  vs. hardcode fallbacks — recommend hardcode first, promote once stable (matches the
  existing `financial-qa` pattern).
- Whether to refactor reusable parts of `run_certification.py` into `cert_common.py`
  now, or copy the score-persist block once and refactor later — recommend the small
  refactor to avoid drift.
- Adversarial compliance items for Agent 3: add to a new
  `sample_data/advisory_adversarial.json` vs. inline in the runner — recommend a
  small dataset file so it's reproducible and auditable.
