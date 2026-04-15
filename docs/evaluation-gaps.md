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

**Status: Todo**

**Problem:** The system prompt is hardcoded in `run_certification.py:_build_prompt()`. Changes to the prompt require code changes and redeployment. There is no version history or ability to A/B test prompt strategies.

**What Langfuse offers:** Prompt Management with:
- Versioning: every edit creates a new immutable version
- Labels: `production`, `staging`, `latest`, or custom labels
- Deployment workflow: test in dev, promote to production, rollback if needed
- Code references labels, so prompt updates don't require code changes

**Action items:**
- [ ] Create a `financial-qa` prompt in Langfuse with the current system prompt text
- [ ] Update `_build_prompt()` to fetch the prompt from Langfuse via `langfuse.get_prompt("financial-qa", label="production")`
- [ ] Create a `financial-sentiment` prompt for the FPB dataset
- [ ] Document the prompt update workflow for the team

**Langfuse reference:** [Prompt Management](https://langfuse.com/docs/prompt-management/get-started)

---

## Gap 5: Production Monitoring (Online Evaluation)

**Status: Todo**

**Problem:** The pipeline only runs offline experiments (batch certification). Once a model is certified and deployed to production, there is no continuous monitoring for quality degradation, compliance violations, or hallucinations in live traffic.

**What Langfuse offers:** Online LLM-as-a-Judge evaluators that run automatically on live traces:
- Observation-level evaluators (recommended): run on individual LLM calls, complete in seconds
- Filter by trace name, tags, user, metadata
- Sampling support (e.g., evaluate 5% of traffic) to manage cost
- Scores feed into dashboards for real-time monitoring

**Action items:**
- [ ] Set up an online `regulatory_compliance` evaluator — deterministic checks are on the Langfuse roadmap ([GitHub discussion](https://github.com/orgs/langfuse/discussions/6087)), but in the meantime this can be done via the SDK as a post-processing step
- [ ] Set up an online `groundedness` LLM-as-a-Judge evaluator with sampling (e.g., 10% of production traces)
- [ ] Create a Custom Dashboard in Langfuse to monitor compliance and groundedness scores over time
- [ ] Define alerting thresholds — if avg groundedness drops below 0.7 in a 1-hour window, flag for review

**Langfuse reference:** [LLM-as-a-Judge for online evaluation](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge), [Custom Dashboards](https://langfuse.com/docs/metrics/features/custom-dashboards)

---

## Gap 6: CI/CD Test File

**Status: Todo**

**Problem:** The README shows a `test_certification.py` pytest example for gating deployments, but the file does not actually exist in the repo. There is no GitHub Actions workflow to run certifications automatically.

**Action items:**
- [ ] Create `tests/test_certification.py` with the pytest gate from the README
- [ ] Parameterize the test for multiple datasets (financebench, fpb)
- [ ] Create `.github/workflows/certification.yml` GitHub Actions workflow:
  - Trigger on: model config changes, prompt updates, manual dispatch
  - Steps: install deps, run certification against sample datasets, fail the workflow if certification fails
- [ ] Add a `--ci` flag to `run_certification.py` that returns exit code 1 on certification failure (currently it always exits 0)
- [ ] Document the CI/CD setup in the README

**Langfuse reference:** [Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
