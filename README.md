# LLM Certification for Financial Services

Automated LLM model certification pipeline using [Langfuse](https://langfuse.com) experiments and open-source financial evaluation datasets. Powered by [ClickHouse](https://clickhouse.com) as the analytics backend.

**The problem:** Certifying a new LLM (e.g., Claude Sonnet 4.6, GPT-4o) for use in financial services takes weeks of manual testing - sending prompts, collecting responses, scoring accuracy, writing compliance reports. Model risk management teams need standardized, reproducible evidence before approving models for production.

**This pipeline:** Load golden financial datasets into Langfuse, run them against any model through a single command, automatically score with financial evaluators, and export results for compliance reports. What took 2 weeks becomes 1 day.

## Architecture

```
+-------------------+     +-------------------+     +-------------------+
|  Golden Datasets  |     |    Experiment     |     |    Evaluators     |
|  (Langfuse)       |---->|    Runner (SDK)   |---->|                   |
|                   |     |                   |     | Deterministic:    |
| - FinanceBench    |     | Calls model under |     | - Numerical acc.  |
| - Financial PB    |     | test, creates     |     | - Exact match     |
| - Custom datasets |     | traces            |     | - Sentiment       |
+-------------------+     +--------+----------+     | - Compliance      |
                                   |                 |                   |
                          +--------v----------+     | LLM-as-a-Judge:   |
                          |  Model Under Test |     | - Groundedness    |
                          |  (any endpoint)   |     +--------+----------+
                          |                   |              |
                          | - Claude Sonnet   |     +--------v----------+
                          | - Claude Haiku    |     |      Results      |
                          | - GPT-4o          |     |                   |
                          | - LLM Gateway     |     | - Scores per item |
                          | - Custom models   |     | - PASS / FAIL     |
                          +-------------------+     | - Audit trail     |
                                                    | - Export to MD/   |
                                                    |   JSON/CSV        |
                                                    +-------------------+
```

## Prerequisites

- Python 3.10+
- A [Langfuse](https://cloud.langfuse.com) instance (Cloud free tier or self-hosted)
- An LLM API key (OpenAI, Anthropic, or any OpenAI-compatible endpoint)

## Quick Start

### 1. Setup

```bash
git clone https://github.com/doneyli/clickhouse-llm-evals-finance.git
cd clickhouse-llm-evals-finance
cp .env.example .env    # Edit with your Langfuse + LLM API credentials
pip install -r requirements.txt
```

### 2. Load Sample Dataset (offline, no HuggingFace needed)

```bash
python setup_datasets.py --dataset financebench --sample
```

### 3. Set Up Score Configs and Annotation Queues

```bash
python setup_score_configs.py        # Register score types in Langfuse
python setup_annotation_queues.py    # Create human review queue
```

### 4. Run Certification

```bash
python run_certification.py --dataset certification/financebench-sample \
  --model claude-sonnet-4-6 --queue-failures
```

### 5. View Results

Open your Langfuse UI > **Datasets** > `certification/financebench-sample` > **Runs**

### 6. Review Failed Items

Open your Langfuse UI > **Annotation Queues** > `Certification Review` to review items that failed automated evaluation.

### 7. Export Report

```bash
python export_results.py --dataset certification/financebench-sample
python export_results.py --dataset certification/financebench-sample --format json --output report.json
```

## Full Dataset Mode

To load all 150 FinanceBench items from HuggingFace (requires internet):

```bash
python setup_datasets.py --dataset financebench        # Downloads from HuggingFace
python run_certification.py --dataset certification/financebench-v1 --model gpt-4o
```

## Components

### `setup_datasets.py` - Dataset Loader

Loads golden financial datasets into Langfuse from HuggingFace or embedded sample files.

```
Options:
  --dataset {financebench,fpb,all}   Which dataset(s) to load (default: all)
  --sample                           Use embedded sample data (offline mode)
  --prefix PREFIX                    Dataset name prefix (default: certification)
  --dry-run                          Preview without creating
```

**Supported datasets:**

| Dataset | Items | Source | Focus |
|---------|-------|--------|-------|
| `financebench` | 10 (sample) / 150 (full) | [PatronusAI/financebench](https://huggingface.co/datasets/PatronusAI/financebench) | Financial QA from SEC filings |
| `fpb` | 10 (sample) / ~4850 (full) | [ChanceFocus/en-fpb](https://huggingface.co/datasets/ChanceFocus/en-fpb) | Financial sentiment classification |

### `setup_score_configs.py` - Score Config Setup

Registers score configurations in Langfuse for all evaluators. This gives scores proper types, value ranges, and descriptions in the Langfuse UI. Also creates human review score configs for annotation queues. Idempotent — safe to re-run.

```
Options:
  --dry-run    Preview configs without creating
```

### `setup_annotation_queues.py` - Annotation Queue Setup

Creates annotation queues in Langfuse for human review of certification results. Queues are linked to the human review score configs. Requires `setup_score_configs.py` to be run first. Idempotent.

```
Options:
  --dry-run    Preview queues without creating
```

### `run_certification.py` - Experiment Runner

Runs a Langfuse dataset through a model, evaluates outputs, and reports pass/fail.

```
Options:
  --dataset DATASET        Langfuse dataset name (required)
  --model MODEL            Model to certify (default: claude-sonnet-4-6)
  --endpoint URL           LLM API base URL (for custom gateways)
  --threshold FLOAT        Pass threshold (default: 0.85)
  --max-concurrency N      Concurrent API calls (default: 5)
  --evaluators {all,...}   Which evaluators to run
  --queue-failures         Route failed items to annotation queue for human review
  --dry-run                Preview dataset items only
```

### `evaluators.py` - Financial Evaluators

Importable module of evaluation functions. All follow the Langfuse SDK signature. The pipeline uses **both** deterministic and LLM-as-a-Judge evaluators — deterministic checks handle objective, verifiable facts (number matching, prohibited phrases), while the LLM judge assesses subjective quality dimensions (groundedness, faithfulness to source documents).

**Deterministic evaluators** (fast, cheap, reproducible):

| Evaluator | Type | What It Checks |
|-----------|------|---------------|
| `numerical_accuracy_evaluator` | Item | Extracts numbers, compares with 5% tolerance |
| `exact_match_evaluator` | Item | Strict string containment |
| `sentiment_evaluator` | Item | Sentiment classification accuracy |
| `regulatory_compliance_evaluator` | Item | Scans for prohibited financial phrases |
| `response_completeness_evaluator` | Item | Response length and structure |

**LLM-as-a-Judge evaluators** (nuanced, catches qualitative failures):

| Evaluator | Type | What It Checks |
|-----------|------|---------------|
| `groundedness_evaluator` | Item | Faithfulness + completeness vs source filing evidence |

The groundedness evaluator sends the model's output, source evidence, and question to a judge model (default: `claude-sonnet-4-6`, configurable via `JUDGE_MODEL` env var) with a financial auditor rubric. It scores **faithfulness** (are claims supported by the documents?) and **completeness** (does the answer cover relevant information?), combined into a weighted score (70% faithfulness, 30% completeness). It only runs on items that include source evidence (e.g., FinanceBench).

**Run-level evaluators** (aggregate across all items):

| Evaluator | Type | What It Checks |
|-----------|------|---------------|
| `average_score_evaluator(name)` | Run | Averages a named score across all items |
| `certification_gate(name, threshold)` | Run | PASS/FAIL based on score threshold |

### `export_results.py` - Report Exporter

Exports experiment scores for compliance/AMRM report generation.

```
Options:
  --dataset DATASET          Langfuse dataset name (required)
  --run-name NAME            Specific run (default: latest)
  --format {markdown,json,csv}  Output format (default: markdown)
  --output FILE              Output file (default: stdout)
```

## Customization

### Adding Your Own Datasets

Create a JSON file with your test cases:

```json
[
  {
    "question": "What was the total revenue for FY2023?",
    "answer": "$52.6 billion",
    "justification": "From the income statement, line: Total Revenue"
  }
]
```

Then load it with `setup_datasets.py` or use the Langfuse SDK directly:

```python
from langfuse import get_client
langfuse = get_client()
langfuse.create_dataset(name="my-custom-dataset")
langfuse.create_dataset_item(
    dataset_name="my-custom-dataset",
    input={"question": "What was the total revenue for FY2023?"},
    expected_output={"answer": "$52.6 billion"},
)
```

### Adding Custom Evaluators

Add a function to `evaluators.py` following the Langfuse signature:

```python
from langfuse import Evaluation

def my_custom_evaluator(*, input, output, expected_output, **kwargs):
    # Your evaluation logic here
    score = 1.0 if "some condition" else 0.0
    return Evaluation(name="my_metric", value=score, comment="Reason")
```

Then import it in `run_certification.py`.

### Changing Pass Thresholds

```bash
python run_certification.py --dataset my-dataset --threshold 0.90
```

Or modify `DEFAULT_THRESHOLD` in `evaluators.py`.

### Using a Custom LLM Gateway

```bash
# Via environment variable
export LLM_BASE_URL="https://your-gateway.internal/v1"
export LLM_API_KEY="your-key"

# Or via CLI flag
python run_certification.py --endpoint https://your-gateway.internal/v1 --dataset ...
```

## Human Review (Annotation Queues)

The pipeline supports human-in-the-loop review for compliance sign-off and evaluator calibration.

### Setup

```bash
python setup_score_configs.py        # Creates human_accuracy and human_groundedness score configs
python setup_annotation_queues.py    # Creates "Certification Review" queue
```

### Routing Failed Items

Pass `--queue-failures` to automatically route low-scoring items to the annotation queue:

```bash
python run_certification.py --dataset certification/financebench-sample \
  --model claude-haiku-4-5-20251001 --queue-failures
```

Items are queued when:
- The primary accuracy score is 0 (completely wrong answer)
- The groundedness score is below 0.5 (poorly grounded in source evidence)

### Reviewer Workflow

1. Open Langfuse UI > **Annotation Queues** > **Certification Review**
2. For each item, the reviewer sees the original question, the model's response, and the source evidence
3. Score `human_accuracy` (Correct / Partially Correct / Incorrect) and `human_groundedness` (Fully Grounded / Partially Grounded / Not Grounded)
4. Click **Complete + next** to proceed

Human annotations serve two purposes:
- **Compliance audit trail** — documented human sign-off on certification results
- **Evaluator calibration** — compare human scores against automated scores to validate the evaluation rubrics

## CI/CD Integration

Gate deployments with pytest:

```python
# test_certification.py
from langfuse import get_client
from run_certification import create_certification_task
from evaluators import numerical_accuracy_evaluator, certification_gate

def test_model_meets_threshold():
    langfuse = get_client()
    dataset = langfuse.get_dataset("certification/financebench-v1")

    result = dataset.run_experiment(
        name="ci-gate",
        task=create_certification_task("claude-sonnet-4-6", "https://api.openai.com/v1", "sk-..."),
        evaluators=[numerical_accuracy_evaluator],
        run_evaluators=[certification_gate("numerical_accuracy", threshold=0.85)],
    )

    cert = next(ev for ev in result.run_evaluations if ev.name == "certification_result")
    assert cert.value == 1.0, f"Certification failed: {cert.comment}"
```

## Expanding to More Financial Datasets

The [Open FinLLM Leaderboard](https://huggingface.co/spaces/TheFinAI/Open-Financial-LLM-Leaderboard) provides 40+ financial datasets. Good next candidates:

| Dataset | Focus | HuggingFace ID |
|---------|-------|----------------|
| FLARE FinQA | Numerical reasoning over financial tables | `ChanceFocus/flare-finqa` |
| FLARE FOMC | Monetary policy stance classification | `ChanceFocus/flare-fomc` |
| Credit Risk (German) | Credit scoring | `ChanceFocus/flare-german` |
| Credit Risk (Taiwan) | Credit risk assessment | `TheFinAI/cra-taiwan` |
| TATQA | Table + text hybrid QA | `ChanceFocus/flare-tatqa` |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LANGFUSE_PUBLIC_KEY` | Yes | — | Langfuse project public key |
| `LANGFUSE_SECRET_KEY` | Yes | — | Langfuse project secret key |
| `LANGFUSE_BASE_URL` | No | `https://cloud.langfuse.com` | Langfuse instance URL |
| `ANTHROPIC_API_KEY` | For Claude models | — | Anthropic API key for Claude models |
| `LLM_API_KEY` | For OpenAI models | — | OpenAI-compatible API key |
| `LLM_BASE_URL` | No | `https://api.openai.com/v1` | LLM API base URL |
| `LLM_MODEL` | No | `claude-sonnet-4-6` | Default model to certify |
| `JUDGE_MODEL` | No | `claude-sonnet-4-6` | Model used by LLM-as-a-Judge evaluators |

## FAQ

### How does certification scoring work?

The pipeline runs the model under test against every item in a Langfuse dataset, then scores each response with a set of evaluators. Scores are aggregated at the run level, and a **certification gate** checks whether the primary accuracy metric meets the configured threshold (default: 85%). The model either PASSES or FAILS.

### Are the evaluators deterministic or LLM-based?

Both. The pipeline uses **deterministic evaluators** (regex, string matching, number extraction) for objective metrics and an **LLM-as-a-Judge evaluator** (`groundedness_evaluator`) for subjective quality assessment. Deterministic evaluators are fast, cheap, and reproducible. The LLM judge catches qualitative failures that heuristics miss — like whether the model hallucinated a number that happens to be correct, or whether it actually used the source documents.

### Why use both types of evaluators?

They cover different failure modes. For example, in our Haiku certification run:
- **Numerical accuracy** (deterministic): 60% — Haiku often gets the numbers wrong
- **Groundedness** (LLM judge): 97% — but when it has evidence, it faithfully uses it

Without the LLM judge, you'd just see "60%, FAILED" and assume the model is unreliable. With it, you can see the failure is specifically in numerical reasoning, not in faithfulness to source material. That distinction matters for model risk assessments.

### Where do certification results appear in Langfuse?

- **Item-level scores** (numerical_accuracy, groundedness, etc.) appear on each trace under the dataset run in **Datasets > [dataset] > Runs**
- **Run-level scores** (certification_result, avg_numerical_accuracy, avg_groundedness) are persisted as scores on the first experiment trace. You can find them by searching for scores named `certification_result` in the Langfuse Scores view, or by clicking into any trace from the dataset run.

### Can I use a different judge model?

Yes. Set the `JUDGE_MODEL` environment variable:

```bash
JUDGE_MODEL=claude-haiku-4-5-20251001 python run_certification.py --dataset ...
```

Using a cheaper/faster judge model reduces cost but may lower evaluation quality. We recommend using a model at least as capable as `claude-sonnet-4-6` for financial evaluations.

### How do I add my own evaluator?

Add a function to `evaluators.py` following the Langfuse SDK signature:

```python
from langfuse import Evaluation

def my_custom_evaluator(*, input, output, expected_output, **kwargs):
    score = 1.0 if "some condition" else 0.0
    return Evaluation(name="my_metric", value=score, comment="Reason")
```

Then import it in `run_certification.py` and add it to `select_evaluators()`.

### What's the difference between item-level and run-level evaluators?

- **Item-level** evaluators score each dataset item individually (e.g., "did this answer match the expected number?")
- **Run-level** evaluators aggregate across all items (e.g., "what was the average accuracy?" or "did the model pass certification?")

### What datasets are supported?

Currently two financial benchmarks are included:

| Dataset | Items | Focus |
|---------|-------|-------|
| [FinanceBench](https://huggingface.co/datasets/PatronusAI/financebench) | 10 (sample) / 150 (full) | Financial QA from SEC filings |
| [Financial PhraseBank](https://huggingface.co/datasets/ChanceFocus/en-fpb) | 10 (sample) / ~4850 (full) | Financial sentiment classification |

You can add custom datasets — see the [Customization](#customization) section.

## Companion Projects

- [clickhouse-llm-observability](https://github.com/doneyli/clickhouse-llm-observability) - Full LLM observability stack with LibreChat, Langfuse, and ClickHouse (monitoring, tracing, debugging)

## References

- [Langfuse Experiments via SDK](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk)
- [Langfuse Datasets](https://langfuse.com/docs/evaluation/experiments/datasets)
- [Langfuse Custom Scores](https://langfuse.com/docs/scores/custom)
- [FinanceBench Paper](https://arxiv.org/abs/2311.11944)
- [Open FinLLM Leaderboard](https://huggingface.co/spaces/TheFinAI/Open-Financial-LLM-Leaderboard)

## License

Apache 2.0
