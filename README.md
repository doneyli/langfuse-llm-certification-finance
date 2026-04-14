# LLM Certification for Financial Services

Automated LLM model certification pipeline using [Langfuse](https://langfuse.com) experiments and open-source financial evaluation datasets. Powered by [ClickHouse](https://clickhouse.com) as the analytics backend.

**The problem:** Certifying a new LLM (e.g., Claude Sonnet 4.6, GPT-4o) for use in financial services takes weeks of manual testing - sending prompts, collecting responses, scoring accuracy, writing compliance reports. Model risk management teams need standardized, reproducible evidence before approving models for production.

**This pipeline:** Load golden financial datasets into Langfuse, run them against any model through a single command, automatically score with financial evaluators, and export results for compliance reports. What took 2 weeks becomes 1 day.

## Architecture

```
+-------------------+     +-------------------+     +-------------------+
|  Golden Datasets  |     |    Experiment     |     |    Evaluators     |
|  (Langfuse)       |---->|    Runner (SDK)   |---->|                   |
|                   |     |                   |     | - Numerical acc.  |
| - FinanceBench    |     | Calls model under |     | - Exact match     |
| - Financial PB    |     | test, creates     |     | - Sentiment       |
| - Custom datasets |     | traces            |     | - Compliance      |
+-------------------+     +--------+----------+     | - Completeness    |
                                   |                 +--------+----------+
                          +--------v----------+               |
                          |  Model Under Test |     +---------v---------+
                          |  (any endpoint)   |     |      Results      |
                          |                   |     |                   |
                          | - Claude Sonnet   |     | - Scores per item |
                          | - GPT-4o          |     | - PASS / FAIL     |
                          | - LLM Gateway     |     | - Audit trail     |
                          | - Custom models   |     | - Export to MD/   |
                          +-------------------+     |   JSON/CSV        |
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

### 3. Run Certification

```bash
python run_certification.py --dataset certification/financebench-sample \
  --model claude-sonnet-4-6
```

### 4. View Results

Open your Langfuse UI > **Datasets** > `certification/financebench-sample` > **Runs**

### 5. Export Report

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
  --dry-run                Preview dataset items only
```

### `evaluators.py` - Financial Evaluators

Importable module of evaluation functions. All follow the Langfuse SDK signature.

| Evaluator | Type | What It Checks |
|-----------|------|---------------|
| `numerical_accuracy_evaluator` | Item | Extracts numbers, compares with 5% tolerance |
| `exact_match_evaluator` | Item | Strict string containment |
| `sentiment_evaluator` | Item | Sentiment classification accuracy |
| `regulatory_compliance_evaluator` | Item | Scans for prohibited financial phrases |
| `response_completeness_evaluator` | Item | Response length and structure |
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
