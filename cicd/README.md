# The quality bar, as code

[`thresholds.json`](thresholds.json) holds every deployment-gate bar in this repo
in one reviewable file. It is the artifact to show someone who asks *"what stops
a bad prompt or a worse model reaching production?"* — the answer should be a
file in git, where loosening a bar shows up as a diff in a pull request, not a
constant buried in an agent module and not a person remembering to check a
dashboard.

## What reads it

| Consumer | Reads |
|---|---|
| [`agents/financial_analyst.py`](../agents/financial_analyst.py), [`sentiment_triage.py`](../agents/sentiment_triage.py), [`advisory_draft.py`](../agents/advisory_draft.py) | `agent_gates.<agent>` at import, via `cert_common.agent_gate_thresholds()`. `run_usecase_certification.py` turns each entry into one average-score evaluator plus a `usecase_certification_gate`. |
| [`run_certification.py`](../run_certification.py) | `model_gate.default_threshold` as the `--threshold` default. |
| [`tests/test_gate_thresholds.py`](../tests/test_gate_thresholds.py) | Everything — it fails if the file and the agent registry diverge, if a gated dimension has no evaluator producing it, or if an entry goes missing. |

Run the gate locally exactly as CI runs it:

```bash
make ci-gate USE_CASE=advisory-draft
```

## Why a missing entry is fatal

`usecase_certification_gate` is `all()` over the dimensions it is handed, and
`all()` over an empty dict is `True`. An agent registered with no thresholds
would therefore report **PASSED unconditionally** — so
`cert_common.agent_gate_thresholds()` raises at import rather than defaulting.
Deleting a bar to quiet a failing dimension is how a gate silently stops being
one; `tests/test_gate_thresholds.py` pins that behaviour down.

## Hard bars vs loose bars

The split is deliberate, and the reasoning is in the file's own `_comment` block:
compliance and must-run tool checks are gated at `1.00` because a prohibited
phrase in client-facing output is a defect rather than a budget to spend, while
the single LLM-as-a-judge dimension is gated loosely at `0.80` because a judge
carries run-to-run noise and a tight judge bar buys flaky gates.

The file also records what **no** bar here covers — persona and tone are not
gated, so a regression in how an answer *reads* cannot fail these gates — and
that the calibrated bars have not been repeat-run variance-tested. Read the
`_comment` before treating a near-miss as a signal.

## What this repo deliberately does *not* do in CI

The gates in [`.github/workflows/certification.yml`](../.github/workflows/certification.yml)
and [`prompt-recert.yml`](../.github/workflows/prompt-recert.yml) need a runner
that can reach Langfuse with credentials. **This repo carries no secrets on
purpose** — it is shared with clients, so `certification.yml` is disabled and
live gate runs happen locally. Everything in this directory works without
credentials: the file is read at import, and the drift tests are pure.

To make the gates actually execute in Actions you would add
`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL` and
`ANTHROPIC_API_KEY` as repository secrets and re-enable the workflow — see
[`docs/loop-activation-checklist.md`](../docs/loop-activation-checklist.md).
A GitHub runner cannot reach a `localhost` Langfuse, so that route means
Langfuse Cloud or a publicly reachable self-hosted instance.

## Prior art

This pattern is lifted from the real-estate demo in
[`clickhouse-llm-observability`](https://github.com/doneyli/clickhouse-llm-observability/tree/main/demos/real-estate/cicd),
whose `thresholds.json` carries a measured variance table this one does not yet
have. Two further pieces from that demo are **not** ported here because they
require the live-credential setup above: a rendered verdict table in the Actions
job summary for the gate itself, and a `deploy` job that shows the
validated-but-not-deployed distinction between a `candidate` and a `production`
prompt label.
