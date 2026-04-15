#!/usr/bin/env python3
"""
Financial Evaluators for LLM Certification

Custom evaluator functions for scoring LLM outputs against financial datasets.
All evaluators follow the Langfuse SDK signature and return Evaluation objects.

Item-level evaluators (deterministic):
    exact_match_evaluator          - Strict string containment check
    numerical_accuracy_evaluator   - Number extraction + tolerance comparison
    sentiment_evaluator            - Sentiment classification accuracy
    regulatory_compliance_evaluator - Prohibited phrase detection
    response_completeness_evaluator - Length and structure scoring

Item-level evaluators (LLM-as-a-Judge):
    groundedness_evaluator         - Faithfulness + completeness vs source evidence

Run-level evaluators (factories):
    average_score_evaluator(name)          - Average a named score across items
    certification_gate(name, threshold)    - PASS/FAIL based on score threshold

Usage:
    from evaluators import (
        numerical_accuracy_evaluator,
        certification_gate,
        average_score_evaluator,
    )
"""

import json
import os
import re

try:
    from langfuse import Evaluation
except ImportError:
    # Minimal fallback for testing without langfuse installed
    from dataclasses import dataclass

    @dataclass
    class Evaluation:
        name: str
        value: float = None
        comment: str = ""

try:
    import anthropic as _anthropic
    _anthropic_client = None

    def _get_anthropic_client():
        global _anthropic_client
        if _anthropic_client is None:
            _anthropic_client = _anthropic.Anthropic()
        return _anthropic_client
except ImportError:
    _anthropic = None
    _get_anthropic_client = None


# --------------- Configuration ---------------

DEFAULT_THRESHOLD = 0.85
NUMERICAL_TOLERANCE = 0.05  # 5% tolerance for numerical comparisons

PROHIBITED_PHRASES = [
    "guaranteed returns",
    "risk-free investment",
    "insider information",
    "we recommend buying",
    "we recommend selling",
    "guaranteed profit",
    "no risk",
    "sure thing",
    "can't lose",
]


# --------------- Helpers ---------------

def _extract_numbers(text: str) -> list[float]:
    """Extract numerical values from text, handling currency, commas, and percentages.

    Examples:
        "$1,577.00"  -> [1577.0]
        "65.4%"      -> [65.4]
        "24.26"      -> [24.26]
        "$11,588.00" -> [11588.0]
        "0.01"       -> [0.01]
    """
    if not text:
        return []

    # Remove currency symbols and commas, keep negative signs
    cleaned = re.sub(r'[$€£¥]', '', text)
    cleaned = cleaned.replace(',', '')

    # Match numbers including decimals and negatives (with optional % suffix)
    pattern = r'-?\d+\.?\d*%?'
    matches = re.findall(pattern, cleaned)

    numbers = []
    for m in matches:
        try:
            val = float(m.rstrip('%'))
            numbers.append(val)
        except ValueError:
            continue

    return numbers


def _numbers_match(expected: list[float], actual: list[float],
                   tolerance: float = NUMERICAL_TOLERANCE) -> tuple[bool, str]:
    """Check if any expected number appears in actual numbers within tolerance."""
    if not expected:
        return False, "No expected numbers found"
    if not actual:
        return False, "No numbers found in output"

    for exp in expected:
        for act in actual:
            if exp == 0 and act == 0:
                return True, f"Exact match: {exp}"
            if exp == 0:
                continue
            if abs(act - exp) / abs(exp) <= tolerance:
                return True, f"Match: expected {exp}, got {act} (within {tolerance:.0%})"

    return False, f"No match: expected {expected}, found {actual}"


# --------------- Item-Level Evaluators ---------------

def exact_match_evaluator(*, output, expected_output, **kwargs):
    """Check if the expected answer string appears in the model output."""
    if not output or not expected_output:
        return Evaluation(name="exact_match", value=0.0, comment="Missing output or expected_output")

    answer = expected_output.get("answer", "") if isinstance(expected_output, dict) else str(expected_output)
    if not answer:
        return Evaluation(name="exact_match", value=0.0, comment="No expected answer defined")

    answer_clean = answer.strip().lower()
    output_clean = output.strip().lower()

    if answer_clean in output_clean:
        return Evaluation(name="exact_match", value=1.0,
                          comment=f"Found exact answer: {answer[:60]}")

    return Evaluation(name="exact_match", value=0.0,
                      comment=f"Expected '{answer[:60]}' not found in output")


def numerical_accuracy_evaluator(*, output, expected_output, **kwargs):
    """Extract numbers from output and expected answer, compare with tolerance.

    This is the primary evaluator for FinanceBench-style financial QA.
    """
    if not output or not expected_output:
        return Evaluation(name="numerical_accuracy", value=0.0,
                          comment="Missing output or expected_output")

    answer = expected_output.get("answer", "") if isinstance(expected_output, dict) else str(expected_output)
    if not answer:
        return Evaluation(name="numerical_accuracy", value=0.0,
                          comment="No expected answer defined")

    expected_nums = _extract_numbers(answer)
    if not expected_nums:
        # Not a numerical question - fall back to string containment
        answer_clean = answer.strip().lower()
        output_clean = output.strip().lower()
        if answer_clean[:20] in output_clean:
            return Evaluation(name="numerical_accuracy", value=1.0,
                              comment="Non-numerical match (string containment)")
        return Evaluation(name="numerical_accuracy", value=0.0,
                          comment=f"Non-numerical, no string match for: {answer[:60]}")

    actual_nums = _extract_numbers(output)
    matched, detail = _numbers_match(expected_nums, actual_nums)

    if matched:
        return Evaluation(name="numerical_accuracy", value=1.0, comment=detail)

    return Evaluation(name="numerical_accuracy", value=0.0,
                      comment=f"Numerical mismatch. {detail}")


def sentiment_evaluator(*, output, expected_output, **kwargs):
    """Compare model's sentiment classification to expected label.

    For Financial PhraseBank (FPB) dataset items.
    """
    if not output or not expected_output:
        return Evaluation(name="sentiment_accuracy", value=0.0,
                          comment="Missing output or expected_output")

    expected = expected_output.get("sentiment", "") if isinstance(expected_output, dict) else str(expected_output)
    expected = expected.strip().lower()

    output_lower = output.strip().lower()

    # Map common model output patterns to labels
    label_map = {
        "positive": ["positive", "bullish", "optimistic", "favorable", "good news"],
        "negative": ["negative", "bearish", "pessimistic", "unfavorable", "bad news", "decline"],
        "neutral": ["neutral", "mixed", "unchanged", "flat", "no clear sentiment"],
    }

    detected = None
    for label, keywords in label_map.items():
        for kw in keywords:
            if kw in output_lower:
                detected = label
                break
        if detected:
            break

    if detected is None:
        return Evaluation(name="sentiment_accuracy", value=0.0,
                          comment=f"Could not detect sentiment in output. Expected: {expected}")

    if detected == expected:
        return Evaluation(name="sentiment_accuracy", value=1.0,
                          comment=f"Correct: {detected}")

    return Evaluation(name="sentiment_accuracy", value=0.0,
                      comment=f"Incorrect: detected '{detected}', expected '{expected}'")


def regulatory_compliance_evaluator(*, output, **kwargs):
    """Scan output for prohibited financial phrases.

    Returns 1.0 if clean, 0.0 if violations found.
    """
    if not output:
        return Evaluation(name="regulatory_compliance", value=1.0,
                          comment="No output to check")

    output_lower = output.lower()
    violations = [phrase for phrase in PROHIBITED_PHRASES if phrase in output_lower]

    if violations:
        return Evaluation(
            name="regulatory_compliance",
            value=0.0,
            comment=f"Violations found: {', '.join(violations)}"
        )

    return Evaluation(name="regulatory_compliance", value=1.0,
                      comment="No prohibited phrases detected")


def response_completeness_evaluator(*, output, **kwargs):
    """Score response based on length and structure.

    Ported from clickhouse-llm-observability/scripts/run-experiments.py.
    """
    if not output:
        return Evaluation(name="completeness", value=0.0, comment="Empty response")

    length = len(output)
    has_structure = any(marker in output for marker in ["```", "- ", "1.", "##", "**"])

    if length < 50:
        score, comment = 0.2, "Very short response"
    elif length < 200:
        score, comment = 0.5, "Brief response"
    elif length < 1000:
        score, comment = 0.8, "Moderate length response"
    else:
        score, comment = 1.0, "Comprehensive response"

    if has_structure:
        score = min(score + 0.1, 1.0)
        comment += " with structured formatting"

    return Evaluation(name="completeness", value=round(score, 2), comment=comment)


# --------------- LLM-as-a-Judge Evaluators ---------------

GROUNDEDNESS_RUBRIC = """\
You are an expert financial auditor evaluating whether an AI assistant's answer \
is grounded in the provided source documents.

<source_documents>
{evidence}
</source_documents>

<question>
{question}
</question>

<assistant_answer>
{output}
</assistant_answer>

Evaluate the assistant's answer on two dimensions:

1. **Faithfulness** (0.0 - 1.0): Is every claim in the answer supported by the \
source documents? Penalize hallucinated facts, invented numbers, or statements \
not traceable to the evidence.

2. **Completeness** (0.0 - 1.0): Does the answer address the key information \
from the source documents that is relevant to the question?

Respond with ONLY a JSON object (no markdown, no extra text):
{{"faithfulness": <float>, "completeness": <float>, "reasoning": "<1-2 sentences>"}}
"""

JUDGE_MODEL = os.getenv("JUDGE_MODEL", "claude-sonnet-4-6")


def groundedness_evaluator(*, input, output, **kwargs):
    """LLM-as-a-Judge: evaluate whether the model output is grounded in source evidence.

    Requires the Anthropic SDK and ANTHROPIC_API_KEY. Returns None (skipped) when
    the evaluator does not apply, since Langfuse SDK rejects scores with value=None.
    Only applies to items that include evidence (e.g., FinanceBench with filing excerpts).
    """
    if _get_anthropic_client is None:
        return None  # SDK not installed, skip score creation

    # Extract evidence and question from input
    if isinstance(input, dict):
        evidence = input.get("evidence", [])
        question = input.get("question", input.get("text", ""))
    else:
        return None  # Not applicable to this item

    if not evidence or not any(evidence):
        return None  # No source evidence to ground against

    if not output:
        return Evaluation(name="groundedness", value=0.0,
                          comment="Empty output")

    evidence_text = "\n\n".join(
        f"[Excerpt {i}] {ev}" for i, ev in enumerate(evidence, 1) if ev
    )

    prompt = GROUNDEDNESS_RUBRIC.format(
        evidence=evidence_text,
        question=question,
        output=output,
    )

    try:
        client = _get_anthropic_client()
        response = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()

        parsed = json.loads(raw)
        faithfulness = float(parsed["faithfulness"])
        completeness = float(parsed["completeness"])
        reasoning = parsed.get("reasoning", "")

        # Combined score: weighted toward faithfulness (more important for finance)
        score = round(0.7 * faithfulness + 0.3 * completeness, 3)

        return Evaluation(
            name="groundedness",
            value=score,
            comment=f"faithfulness={faithfulness}, completeness={completeness}. {reasoning}",
        )
    except (json.JSONDecodeError, KeyError) as e:
        return Evaluation(name="groundedness", value=0.0,
                          comment=f"Judge response parse error: {e}. Raw: {raw[:200]}")
    except Exception as e:
        return Evaluation(name="groundedness", value=0.0,
                          comment=f"Judge call failed: {e}")


# --------------- Run-Level Evaluators ---------------

def average_score_evaluator(score_name: str):
    """Factory: create a run-level evaluator that averages a named score.

    Ported from clickhouse-llm-observability/scripts/run-experiments.py.

    Usage:
        run_evaluators=[average_score_evaluator("numerical_accuracy")]
    """
    def evaluator(*, item_results, **kwargs):
        values = [
            ev.value for result in item_results
            for ev in result.evaluations
            if ev.name == score_name and ev.value is not None
        ]
        if not values:
            return Evaluation(name=f"avg_{score_name}", value=None,
                              comment="No scores to average")
        avg = sum(values) / len(values)
        return Evaluation(
            name=f"avg_{score_name}",
            value=round(avg, 3),
            comment=f"Average {score_name}: {avg:.1%} across {len(values)} items"
        )
    return evaluator


def certification_gate(score_name: str, threshold: float = DEFAULT_THRESHOLD):
    """Factory: create a run-level evaluator that returns PASS/FAIL.

    Usage:
        run_evaluators=[certification_gate("numerical_accuracy", threshold=0.85)]
    """
    def evaluator(*, item_results, **kwargs):
        values = [
            ev.value for result in item_results
            for ev in result.evaluations
            if ev.name == score_name and ev.value is not None
        ]
        if not values:
            return Evaluation(name="certification_result", value=0.0,
                              comment="FAILED - no scores collected")

        avg = sum(values) / len(values)
        passed = avg >= threshold

        return Evaluation(
            name="certification_result",
            value=1.0 if passed else 0.0,
            comment=f"{'PASSED' if passed else 'FAILED'} - "
                    f"avg {score_name}: {avg:.1%} "
                    f"({'above' if passed else 'below'} {threshold:.0%} threshold, "
                    f"{len(values)} items)"
        )
    return evaluator
