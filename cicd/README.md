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
| [`agents/financial_analyst.py`](../agents/financial_analyst.py), [`sentiment_triage.py`](../agents/sentiment_triage.py), [`advisory_draft.py`](../agents/advisory_draft.py) | `agent_gates.<agent>` at import, via `cert_common.agent_gate_thresholds()` (required bars) and `cert_common.agent_advisory_thresholds()` (advisory bars). `run_usecase_certification.py` turns each bar into one average-score evaluator, plus one `usecase_certification_gate` over all of them. |
| [`run_certification.py`](../run_certification.py) | `model_gate.default_threshold` as the `--threshold` default. |
| [`tests/test_gate_thresholds.py`](../tests/test_gate_thresholds.py) | Everything — it fails if the file and the agent registry diverge, if a gated dimension has no evaluator producing it, if an entry goes missing or has no required bar, or if an advisory bar can change a verdict. |

Run the gate locally exactly as CI runs it:

```bash
make ci-gate USE_CASE=advisory-draft
```

## Required bars and advisory bars

Each bar in `agent_gates.<agent>` is one of two kinds:

```json
"advisory-draft": {
  "groundedness": 0.80,
  "regulatory_compliance": 1.00,
  "completeness": {"threshold": 0.70, "required": false},
  "tool_use_correctness": 1.00
}
```

- **Required** (a bare number, or `{"threshold": x}` / `{"threshold": x, "required": true}`).
  The verdict is `all()` over these: one required bar below its threshold fails
  the run, and `--ci` exits 1.
- **Advisory** (`{"threshold": x, "required": false}`). Measured exactly like a
  required bar (an `avg_<dim>` score on the experiment), reported as `MET` or
  `MISS` in the `certification_result` comment and in the runner summary, and
  recorded in the experiment's metadata. It never changes the verdict or the
  exit code.

A file with only bare numbers means what it always meant: every bar required.

In run metadata the two tiers live under separate keys: required bars under
`gate_thresholds`, advisory bars under `advisory_thresholds`. That split is
load-bearing. The portal and the evidence pack replay an older run's verdict as
`all()` over `gate_thresholds` (`cert_common.recorded_gate`), so an advisory bar
recorded there would fail a run the gate passed. Both readers show the
advisory bars from their own key (`cert_common.recorded_advisory`): the portal
counts them apart from the gate dims and draws a miss amber rather than red, and
the evidence pack gives them an **Advisory** row and `MET`/`MISS (advisory)` in
its Bar column.

Use advisory for a signal worth watching on every run that should not block a
release on its own: a heuristic, or a new evaluator still being calibrated.
`advisory-draft`'s `completeness` is the example here, since it is a length and
structure heuristic rather than a correctness or safety property. Moving a bar
from required to advisory loosens the gate, so review that diff the way you
would review deleting the bar.

A malformed entry (an unknown key such as a misspelled `"requried"`, a missing
`threshold`, a non-boolean `required`) raises at import rather than being
guessed at.

## Why a missing entry is fatal

`usecase_certification_gate` is `all()` over the required dimensions it is
handed, and `all()` over an empty dict is `True`. An agent registered with no
required thresholds would therefore report **PASSED unconditionally**, whatever
its advisory bars say. So `cert_common.agent_gate_thresholds()` raises at import
on a missing or empty entry, and on an entry whose bars are all advisory, rather
than defaulting. Deleting a bar to quiet a failing dimension is how a gate
silently stops being one; `tests/test_gate_thresholds.py` pins that behaviour
down.

## Hard bars vs loose bars

The split is deliberate, and the reasoning is in the file's own `_comment` block:
compliance and must-run tool checks are gated at `1.00` because a prohibited
phrase in client-facing output is a defect rather than a budget to spend, while
the single LLM-as-a-judge dimension is gated loosely at `0.80` because a judge
carries run-to-run noise and a tight judge bar buys flaky gates.

## The verdict is a boolean score

Each run's verdict is written to Langfuse as the run-level score
`certification_result`, a **BOOLEAN** (`true` = every required bar in this file
cleared, `false` = at least one did not), stored on the experiment with a
per-dimension breakdown in its comment (required bars as `PASS`/`FAIL`, then
advisory bars as `MET`/`MISS`). The bars themselves are recorded in the
experiment's metadata, so a historical run reports the bar it was judged by.

The Langfuse SDK writes every run-level score (`certification_result` and each
`avg_<dim>`) itself, with `datasetRunId` set to the experiment id and no
`traceId`. That is what makes them experiment scores: they come back from
`GET /api/public/experiments?fields=scores` and from
`GET /api/public/v3/scores?experimentId=<experiment id>`, because the
`experimentId` filter matches a score's dataset run id. A pipeline step waiting
on the verdict can poll either. Do not re-post run-level scores yourself with a
`traceId`: that stores a second copy as an item score on whichever trace you
picked, which `experimentId` filtering cannot see. This repo did exactly that
until the SDK v5 cutover (`cert_common.persist_run_evaluations`, removed). It
was a duplicate even on SDK 3.14, which already wrote run-level scores to the
dataset run.

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
