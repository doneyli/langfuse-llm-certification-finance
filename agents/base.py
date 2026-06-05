#!/usr/bin/env python3
"""
Shared infrastructure for use-case (agent) certification.

Where model certification runs a single LLM call per dataset item, use-case
certification runs a multi-step *agent* (plan -> retrieve -> compute -> compose)
and certifies the whole system against several production-readiness bars at once.

This module provides what every agent needs:

  AgentResult            - the structured result every agent task returns
                           (answer + trajectory), so existing evaluators read
                           `.answer` and the trajectory evaluator reads `.trajectory`.
  traced_generation      - one LLM call wrapped in a Langfuse *generation* span,
                           recording model + token usage. Fixes the untraced
                           native-Anthropic path used by the model-cert runner.
  traced_span / traced_tool - context managers for non-LLM agent steps.
  AGENT_REGISTRY / register_agent - the use-case registry the runner dispatches on.

Agents (the use cases themselves) live in sibling modules and self-register on
import:
  agents/financial_analyst.py  (10k-analyst)     - GitHub issue #9
  agents/sentiment_triage.py   (sentiment-triage) - GitHub issue #10
  agents/advisory_draft.py     (advisory-draft)   - GitHub issue #11

See docs/usecase-certification.md for the full spec.
"""

from dataclasses import dataclass, field

try:
    from langfuse import get_client
except ImportError:  # pragma: no cover - langfuse is a hard dependency in practice
    get_client = None

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None


# --------------- Structured result contract ---------------

@dataclass
class AgentResult:
    """The value every agent task returns (via ``to_output()``).

    ``answer`` is the final answer text — what the numeric/sentiment/compliance
    evaluators read. ``trajectory`` records *how* the agent got there so the
    deterministic ``tool_use_correctness_evaluator`` can certify the path, not
    just the answer.

    trajectory schema (all keys optional, agents populate what applies):
        question_type:      str        e.g. "Numerical reasoning" / "sentiment" / "advisory"
        steps:              list[str]  ordered span names actually executed
        tools_used:         list[str]  e.g. ["calculate"]
        operands:           dict       numbers pulled from evidence (audit trail)
        citations:          list[str]  line items / excerpts the answer rests on
        compliance_checked: bool       (advisory agent) whether the self-check ran
    """

    answer: str
    trajectory: dict = field(default_factory=dict)

    def to_output(self) -> dict:
        return {"answer": self.answer, "trajectory": self.trajectory}


# --------------- Traced LLM call (native Anthropic, with spans) ---------------

_anthropic_client = None


def _client():
    global _anthropic_client
    if _anthropic_client is None:
        if anthropic is None:
            raise RuntimeError(
                "anthropic package not installed. Run: pip install anthropic"
            )
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


def traced_generation(*, name: str, system: str, user: str, model: str,
                      max_tokens: int = 1024) -> str:
    """One LLM call wrapped in a Langfuse generation span (model, usage, IO).

    The raw ``anthropic.Anthropic()`` client emits no spans; this wrapper opens a
    generation observation so each agent LLM step shows up in the trace tree with
    model name and token usage. Inside a ``run_experiment`` task there is already
    an active trace per item, so this generation nests under it automatically.
    """
    lf = get_client()
    with lf.start_as_current_observation(
        as_type="generation",
        name=name,
        model=model,
        input={"system": system, "user": user},
    ) as gen:
        resp = _client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = resp.content[0].text
        gen.update(
            output=text,
            usage_details={
                "input": resp.usage.input_tokens,
                "output": resp.usage.output_tokens,
            },
        )
        return text


def traced_span(name: str, **kwargs):
    """Context manager for a non-LLM agent step (retrieval, extraction)."""
    return get_client().start_as_current_observation(as_type="span", name=name, **kwargs)


def traced_tool(name: str, **kwargs):
    """Context manager for a deterministic tool call (calculator, router, scanner)."""
    return get_client().start_as_current_observation(as_type="tool", name=name, **kwargs)


# --------------- Use-case registry ---------------

# Populated by agent modules calling register_agent() at import time.
# Shape: name -> {fn, gate_thresholds, item_evaluators, dataset_hint, description}
AGENT_REGISTRY: dict = {}


def register_agent(name: str, *, fn, gate_thresholds: dict, item_evaluators: list,
                   dataset_hint: str = "", description: str = "") -> None:
    """Register a use-case agent so ``run_usecase_certification.py`` can dispatch it.

    Args:
        name:             use-case slug, e.g. "10k-analyst"
        fn:               factory ``fn(*, model, **opts) -> task(*, item, **kwargs)``
        gate_thresholds:  {score_name: min_threshold} — the multi-dimensional gate.
                          The runner builds an avg-score evaluator per dimension and
                          a ``usecase_certification_gate`` from this single source.
        item_evaluators:  list of item-level evaluator callables to run.
        dataset_hint:     which dataset family this use case targets (docs only).
        description:      one-line human description.
    """
    AGENT_REGISTRY[name] = {
        "fn": fn,
        "gate_thresholds": dict(gate_thresholds),
        "item_evaluators": list(item_evaluators),
        "dataset_hint": dataset_hint,
        "description": description,
    }
