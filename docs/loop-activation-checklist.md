# Loop Activation Checklist

Everything for the [AI Engineering Loop](ai-engineering-loop.md) is **code-complete
on `main`**. What remains is the one-time **console/config** to turn it on — the
steps that can't live in the repo because they involve Langfuse-UI automations,
GitHub secrets, and a personal access token. Work top to bottom; each section says
how to verify it.

> Substitute your own project throughout. The GitHub repo used in the examples is
> `doneyli/langfuse-llm-certification-finance`; the Langfuse base URL is whatever
> your `.env` `LANGFUSE_BASE_URL` points at (Cloud or self-hosted).

> **Note:** the repo's tests are **offline** (mocked). Working through §3–§4 below
> is therefore also the **first end-to-end run of the feedback edges against a live
> Langfuse** — i.e. this checklist is what promotes edges A/B from "implemented" to
> "verified". Watch for the verifications called out in each section.

---

## 0. Prerequisites

- [ ] `.env` has `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`,
      and `ANTHROPIC_API_KEY`.
- [ ] `uv sync` (or `pip install -r requirements.txt`).

---

## 1. Base pipeline (one-time) — required before anything can re-certify

Re-certification and the feedback edges need the datasets, prompts, score configs,
and review queue to exist in Langfuse. All are idempotent.

- [ ] `uv run python setup_datasets.py --dataset financebench --sample`
- [ ] `uv run python setup_datasets.py --dataset fpb --sample`
- [ ] `uv run python setup_prompts.py`
- [ ] `uv run python setup_score_configs.py`
- [ ] `uv run python setup_annotation_queues.py`

**Verify (Langfuse UI):** Datasets shows `certification/financebench-sample` +
`certification/fpb-sample`; Prompts shows the `usecase-*` + `financial-*` templates
(all `production`-labelled); Settings → Score Configs has `tool_use_correctness`;
Annotation Queues has `Certification Review`.

---

## 2. GitHub Actions secrets — required for any CI re-certification

Both `certification.yml` and `prompt-recert.yml` read these.

- [ ] Repo → **Settings → Secrets and variables → Actions → New repository secret**,
      add: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`,
      `ANTHROPIC_API_KEY`.
- [ ] If `LANGFUSE_BASE_URL` is a **self-hosted** instance, confirm GitHub-hosted
      runners can reach it (public hostname / tunnel / self-hosted runner). Cloud
      needs nothing extra.

**Verify:** Actions → **LLM Certification** → Run workflow → it runs against your
Langfuse without an auth error.

---

## 3. Activate Edge B — prompt promotion → automatic re-certification

Makes a prompt promotion in Langfuse fire `.github/workflows/prompt-recert.yml`.

- [ ] **Create a GitHub token.** Fine-grained PAT scoped to this repo with
      **Actions: Read and write** (or a classic PAT with `repo` scope).
- [ ] **Create the Langfuse automation.** Project → **Prompts → Automations →
      Create Automation → GitHub Repository Dispatch**:
  - **Dispatch URL:** `https://api.github.com/repos/doneyli/langfuse-llm-certification-finance/dispatches`
  - **Event Type:** `langfuse-prompt-update`  *(must match `types:` in the workflow)*
  - **GitHub Token:** paste the PAT (stored encrypted).
  - *(optional)* Filter events to **`created`** only — see the double-dispatch note
    below.
- [ ] **Test it.** In Langfuse, move the `production` label of a managed prompt
      (e.g. `usecase-advisory-draft`) to a new version. Then GitHub → **Actions →
      Prompt Re-Certification** should show a run that goes green (gate passed) or
      red (gate failed → the promotion regressed the use case).
- [ ] **Or test without Langfuse:** Actions → Prompt Re-Certification → **Run
      workflow** → `prompt_name = usecase-advisory-draft`.

**Good to know (already handled in the workflow):**
- *Double dispatch* — a label move fires **two** dispatches (the version gaining
  `production` and the one losing it). The job runs only for the version that now
  carries `production`, so it re-certifies the deployed version exactly once.
- *Payload truncation* — GitHub truncates large `client_payload`, so routing is by
  prompt **name**; the run fetches the live `production` prompt from Langfuse itself.

---

## 4. Operate Edge A — production failure → golden data

No config to enable — this is an operational workflow (see the runbook's
[Close the loop](usecase-runbook.md#5-inspect-results) recipe). Optionally automate
step 1.

- [ ] *(optional)* Schedule monitoring with queue routing (cron):
      `*/15 * * * * cd /path/to/repo && uv run python monitor_production.py --hours 1 --tags production --queue-violations`
- [ ] Review flagged traces: Langfuse → **Annotation Queues → Certification Review**;
      decide the correct answer for real failures.
- [ ] Promote a reviewed trace into the golden dataset (human-gated):
      `uv run python promote_trace_to_dataset.py --dataset certification/financebench-sample --from-queue --expected "<correct answer>"`
- [ ] Re-certify so the scenario is now a regression test:
      `uv run python run_usecase_certification.py --use-case 10k-analyst --dataset certification/financebench-sample --ci`

**Verify:** the dataset gains a `prod-<traceId>` item (metadata
`promoted_from=production`); the next run's item count includes it.

---

## 5. (Optional) Edge B audit archive — sync prompt versions to git

Commits every prompt version to git as an auditable archive (separate from the
re-certification trigger in §3). This needs a **hosted webhook server**, so it is
out of scope for this repo — follow the Langfuse guide if you want it:

- [ ] Deploy the sample FastAPI sync server (Render/Fly/Heroku/…), set
      `GITHUB_TOKEN` (Contents: read/write), `GITHUB_REPO_*`, and optionally
      `REQUIRED_LABEL=production`.
- [ ] Langfuse → **Prompts → Webhooks → Create Webhook**, point at the server, save
      the signing secret, and verify the `x-langfuse-signature` header server-side.
- Reference: <https://langfuse.com/docs/prompt-management/features/github-integration>

---

## Verification matrix — "is it on?"

| Capability | How to confirm |
|---|---|
| Base pipeline | The four `setup_*` verifications in §1 |
| CI re-cert can auth | LLM Certification workflow runs green (§2) |
| Edge B live | Promote a prompt → Prompt Re-Certification workflow fires (§3) |
| Edge A monitor→queue | `monitor_production.py --queue-violations` adds items to Certification Review |
| Edge A trace→dataset | `promote_trace_to_dataset.py` creates a `prod-<traceId>` item |
| Prompt audit archive | (only if §5 done) a commit appears per prompt version |

---

## What stays manual — by design

- **Edge A expected answers.** A human decides the correct answer for a promoted
  trace (or fills `needs_expected_review` in the UI). We never treat a flagged
  trace's output as ground truth.
- **One-click UI promotion.** Promotion is a deliberate CLI step, not a UI button —
  the right friction for changing what counts as "golden" in a regulated use case.
