"""
Certification Gate Tests

Pytest tests that run LLM certification experiments and assert that the model
meets the required accuracy threshold. Designed for CI/CD pipelines — a test
failure means the model should not be deployed.

Usage:
    pytest tests/test_certification.py -v
    pytest tests/test_certification.py -v -k financebench
    pytest tests/test_certification.py -v --threshold 0.90

Prerequisites:
    - Langfuse running with datasets loaded (setup_datasets.py)
    - LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL set
    - ANTHROPIC_API_KEY set (for Claude models)

Environment variables:
    CERT_MODEL       Model to test (default: claude-haiku-4-5-20251001)
    CERT_THRESHOLD   Pass threshold (default: 0.85)
"""

import os
import sys

import pytest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langfuse import get_client
from run_certification import create_certification_task
from evaluators import (
    numerical_accuracy_evaluator,
    exact_match_evaluator,
    sentiment_evaluator,
    regulatory_compliance_evaluator,
    response_completeness_evaluator,
    groundedness_evaluator,
    average_score_evaluator,
    certification_gate,
)

# --------------- Configuration ---------------

MODEL = os.getenv("CERT_MODEL", "claude-haiku-4-5-20251001")
THRESHOLD = float(os.getenv("CERT_THRESHOLD", "0.85"))
ENDPOINT = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
API_KEY = os.getenv("LLM_API_KEY", os.getenv("OPENAI_API_KEY", ""))


# --------------- Fixtures ---------------

@pytest.fixture(scope="session")
def langfuse():
    """Initialize Langfuse client for the test session."""
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass
    return get_client()


# --------------- Tests ---------------

class TestFinanceBenchCertification:
    """Certification tests for financial QA (FinanceBench)."""

    DATASET = "certification/financebench-sample"

    def test_numerical_accuracy_meets_threshold(self, langfuse):
        """Model must meet the numerical accuracy threshold on FinanceBench."""
        dataset = langfuse.get_dataset(self.DATASET)

        result = dataset.run_experiment(
            name=self.DATASET.split("/")[-1],
            run_name=f"ci-{MODEL}-financebench-accuracy",
            task=create_certification_task(MODEL, ENDPOINT, API_KEY),
            evaluators=[numerical_accuracy_evaluator, exact_match_evaluator],
            run_evaluators=[
                average_score_evaluator("numerical_accuracy"),
                certification_gate("numerical_accuracy", THRESHOLD),
            ],
            max_concurrency=5,
        )

        cert = next(
            (ev for ev in result.run_evaluations if ev.name == "certification_result"),
            None,
        )
        assert cert is not None, "No certification_result in run evaluations"
        assert cert.value == 1.0, f"Certification FAILED: {cert.comment}"

    def test_regulatory_compliance(self, langfuse):
        """Model must not produce any prohibited financial phrases."""
        dataset = langfuse.get_dataset(self.DATASET)

        result = dataset.run_experiment(
            name=self.DATASET.split("/")[-1],
            run_name=f"ci-{MODEL}-financebench-compliance",
            task=create_certification_task(MODEL, ENDPOINT, API_KEY),
            evaluators=[regulatory_compliance_evaluator],
            run_evaluators=[
                average_score_evaluator("regulatory_compliance"),
                certification_gate("regulatory_compliance", 1.0),
            ],
            max_concurrency=5,
        )

        cert = next(
            (ev for ev in result.run_evaluations if ev.name == "certification_result"),
            None,
        )
        assert cert is not None, "No certification_result in run evaluations"
        assert cert.value == 1.0, f"Compliance FAILED: {cert.comment}"


class TestFPBCertification:
    """Certification tests for sentiment classification (Financial PhraseBank)."""

    DATASET = "certification/fpb-sample"

    def test_sentiment_accuracy_meets_threshold(self, langfuse):
        """Model must meet the sentiment accuracy threshold on FPB."""
        dataset = langfuse.get_dataset(self.DATASET)

        result = dataset.run_experiment(
            name=self.DATASET.split("/")[-1],
            run_name=f"ci-{MODEL}-fpb-accuracy",
            task=create_certification_task(MODEL, ENDPOINT, API_KEY),
            evaluators=[sentiment_evaluator],
            run_evaluators=[
                average_score_evaluator("sentiment_accuracy"),
                certification_gate("sentiment_accuracy", THRESHOLD),
            ],
            max_concurrency=5,
        )

        cert = next(
            (ev for ev in result.run_evaluations if ev.name == "certification_result"),
            None,
        )
        assert cert is not None, "No certification_result in run evaluations"
        assert cert.value == 1.0, f"Certification FAILED: {cert.comment}"
