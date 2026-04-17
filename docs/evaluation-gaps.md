# Evaluation Pipeline Gaps & Roadmap

Identified gaps in the current LLM certification pipeline, with recommended actions based on [Langfuse documentation](https://langfuse.com/docs/evaluation/overview).

> Status key: **Done** | **In Progress** | **Todo**

---

## Gap 1: LLM-as-a-Judge Evaluators

**Status: Done**

**Problem:** All evaluators were deterministic (regex, string matching, keyword lists). They cannot assess subjective quality dimensions like whether an answer is actually grounded in the source evidence or whether reasoning is sound.

**What we added:** `groundedness_evaluator` in `evaluators.py` — an LLM-as-a-Judge evaluator that sends the model's output, the source filing evidence, and the question to a judge model (default: `claude-sonnet-4-6`) with a rubric. The judge scores:
- **Faithfulness** (0.0-1.0): Is every claim supported by the source documents?
- **Completeness** (0.0-1.0): Does the answer cover the relevant information?

Combined into a weighted score (70% faithfulness, 30% completeness) — weighted toward faithfulness because hallucinated financial data is worse than an incomplete answer.

**Langfuse reference:** [LLM-as-a-Judge](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)

**Future work:**
- [ ] Add a `justification_quality` judge evaluator — does the model explain *why* the answer is correct, citing specific line items?
- [ ] Configure the judge model via `JUDGE_MODEL` env var (already supported) and document recommended judge models
- [ ] Calibrate judge scores against human annotations to validate the rubric
- [ ] Consider using Langfuse's managed evaluators (Ragas faithfulness, context relevance) once available for self-hosted

---

## Gap 2: Score Configs in Langfuse

**Status: Done**

**Problem:** We wrote scores with ad-hoc names (`numerical_accuracy`, `exact_match`, `groundedness`, etc.) but hadn't created Score Configs in Langfuse. Without them, scores were unstructured — no validation, no defined ranges, no descriptions for team members viewing results in the UI.

**What we added:** `setup_score_configs.py` — a script that creates Score Configs for all 10 evaluator scores via the Langfuse REST API. Each config defines the data type (NUMERIC), value range (0.0-1.0), and a human-readable description. The script is idempotent — re-running it skips existing configs.

**Configs created:**
- [x] `numerical_accuracy` — NUMERIC, 0.0-1.0
- [x] `exact_match` — NUMERIC, 0.0-1.0
- [x] `sentiment_accuracy` — NUMERIC, 0.0-1.0
- [x] `regulatory_compliance` — NUMERIC, 0.0-1.0
- [x] `completeness` — NUMERIC, 0.0-1.0
- [x] `groundedness` — NUMERIC, 0.0-1.0
- [x] `avg_numerical_accuracy` — NUMERIC, 0.0-1.0
- [x] `avg_sentiment_accuracy` — NUMERIC, 0.0-1.0
- [x] `avg_groundedness` — NUMERIC, 0.0-1.0
- [x] `certification_result` — NUMERIC, 0.0-1.0

**Langfuse reference:** [Score Configs API](https://api.reference.langfuse.com) — `POST /api/public/score-configs`

---

## Gap 3: Annotation Queues (Human Review)

**Status: Done**

**Problem:** The pipeline was fully automated with no human-in-the-loop review. For financial services certification, compliance teams need the ability to manually review edge cases and sign off on results.

**What we added:**

1. **Human review score configs** (`setup_score_configs.py`):
   - `human_accuracy` — CATEGORICAL: Correct (1.0) / Partially Correct (0.5) / Incorrect (0.0)
   - `human_groundedness` — CATEGORICAL: Fully Grounded (1.0) / Partially Grounded (0.5) / Not Grounded (0.0)

2. **Annotation queue setup** (`setup_annotation_queues.py`):
   - Creates a "Certification Review" queue linked to the human review score configs
   - Idempotent — skips if queue already exists

3. **Auto-routing of failed items** (`run_certification.py --queue-failures`):
   - After a certification run, items where `primary_score = 0` or `groundedness < 0.5` are automatically routed to the annotation queue
   - Reviewers see the items in the Langfuse UI under Annotation Queues, score them using the human rubric, and click "Complete + next"

**Human review workflow:**
1. Run certification: `python run_certification.py --dataset ... --queue-failures`
2. Open Langfuse UI > Annotation Queues > "Certification Review"
3. For each queued trace, reviewer sees the question, model output, and source evidence
4. Reviewer scores `human_accuracy` and `human_groundedness` using the category rubrics
5. Click "Complete + next" to move to the next item

**Future work:**
- [ ] Compare human annotations against automated scores to measure evaluator quality
- [ ] Set up inter-annotator agreement tracking for multiple reviewers

**Langfuse reference:** [Annotation Queues](https://langfuse.com/docs/evaluation/evaluation-methods/annotation-queues), [Manual Scores via UI](https://langfuse.com/docs/evaluation/evaluation-methods/scores-via-ui)

---

## Gap 4: Prompt Management

**Status: Done**

**Problem:** The system prompt was hardcoded in `run_certification.py:_build_prompt()`. Changes required code changes and redeployment. No version history or A/B testing.

**What we added:**

1. **`setup_prompts.py`** — creates two prompt templates in Langfuse with `production` labels:
   - `financial-qa` — system prompt for financial QA with `{{evidence}}` and `{{question}}` variables
   - `financial-sentiment` — sentiment classification prompt with `{{text}}` variable

2. **`_build_prompt()` updated** — now fetches the `production`-labeled prompt from Langfuse at runtime using `langfuse.get_prompt()`. Falls back to hardcoded templates if Langfuse is unavailable, so the pipeline works without prompt management configured.

**Prompt update workflow:**
1. Open Langfuse UI > **Prompts** > select a prompt (e.g., `financial-qa`)
2. Edit the prompt text — this creates a new immutable version
3. Test the new version by running a certification experiment (it picks up `latest` by default)
4. When satisfied, move the `production` label to the new version
5. All future certification runs automatically use the new prompt — no code changes needed
6. To roll back, reassign the `production` label back to a previous version

**Langfuse reference:** [Prompt Management](https://langfuse.com/docs/prompt-management/get-started)

---

## Gap 5: Production Monitoring (Online Evaluation)

**Status: Done**

**Problem:** The pipeline only ran offline experiments (batch certification). Once a model is certified and deployed to production, there was no continuous monitoring for quality degradation or compliance violations.

**What we added:**

1. **`monitor_production.py`** — a script that fetches recent production traces from Langfuse, runs deterministic evaluators (`regulatory_compliance`, `completeness`) on any unscored traces, and posts scores back. Designed to run on a schedule (e.g., cron every 15 minutes).
   - Filters by lookback window (`--hours`), tags (`--tags production`), or trace name (`--trace-name`)
   - Skips traces that already have compliance scores (idempotent)
   - Exits with code 1 if compliance violations are detected (for alerting integration)
   - Supports `--dry-run` for preview

2. **Usage examples:**
   ```bash
   # Score unscored traces from the last hour
   python monitor_production.py

   # Monitor production-tagged traces from the last 24h
   python monitor_production.py --hours 24 --tags production

   # Cron: every 15 minutes, score new traces
   */15 * * * * cd /path/to/repo && python monitor_production.py --hours 1
   ```

**Future work:**
- [ ] Set up online `groundedness` LLM-as-a-Judge evaluator in the Langfuse UI with sampling (e.g., 10% of production traces) — this requires an LLM Connection configured in Langfuse Settings
- [ ] Create a Custom Dashboard in Langfuse to monitor compliance and groundedness scores over time
- [ ] Integrate with alerting (e.g., PagerDuty/Slack) when `monitor_production.py` exits with code 1

**Langfuse reference:** [LLM-as-a-Judge for online evaluation](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge), [Custom Dashboards](https://langfuse.com/docs/metrics/features/custom-dashboards)

---

## Gap 6: CI/CD Test File

**Status: Done**

**Problem:** The README showed a `test_certification.py` pytest example for gating deployments, but the file did not exist. No GitHub Actions workflow existed.

**What we added:**

1. **`--ci` flag** on `run_certification.py` — exits with code 1 if certification fails, enabling use as a CI gate.

2. **`tests/test_certification.py`** — pytest tests that run certification experiments:
   - `TestFinanceBenchCertification::test_numerical_accuracy_meets_threshold`
   - `TestFinanceBenchCertification::test_regulatory_compliance`
   - `TestFPBCertification::test_sentiment_accuracy_meets_threshold`
   - Configurable via `CERT_MODEL` and `CERT_THRESHOLD` env vars

3. **`.github/workflows/certification.yml`** — GitHub Actions workflow:
   - Manual dispatch with configurable model and threshold inputs
   - Auto-triggers on push to `main` when evaluators, prompts, or config change
   - Runs FinanceBench and FPB certification in parallel with `--ci`
   - Runs pytest gate after both complete
   - Requires secrets: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`, `ANTHROPIC_API_KEY`

**Langfuse reference:** [Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
