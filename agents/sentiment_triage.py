#!/usr/bin/env python3
"""
Use Case 2 — Market Sentiment Triage Agent (Financial PhraseBank).

A news/headline monitoring agent for a trading desk: it classifies the sentiment
of a financial statement, extracts the phrase driving that sentiment, and *routes*
low-confidence items to a human analyst instead of auto-accepting them. The
routing tool is what makes this a triage *system* rather than a single classifier
call — it is the production safeguard, so it must run on every item.

Trace per dataset item (observations nest under the per-item run_experiment trace):

    usecase:sentiment-triage                 trace
    ├── classify   (generation)  text -> "<label> | <confidence>"
    ├── rationale  (span)        deterministic: phrase driving the sentiment
    └── route      (tool)        confidence >= threshold -> auto-accept, else flag-for-analyst

Design note — prompt ownership:
  The classify step's output is parsed ("<label> | <confidence>"), but the parser
  (`_parse_label_conf`) is deliberately lenient, so the prompt is still fetched from
  Langfuse prompt management (usecase-sentiment-classify) and editable in the UI —
  format drift degrades to a safe default rather than crashing. rationale and route
  are deterministic (no LLM), so the routing decision is auditable and reproducible.

Implements GitHub issue #10. Claude-only for now (the agent calls Claude via the
native Anthropic SDK through traced_generation).
"""

import re

from agents.base import (
    AgentResult,
    register_agent,
    traced_generation,
    traced_span,
    traced_tool,
)
from cert_common import agent_gate_thresholds, get_managed_prompt
from evaluators import (
    sentiment_evaluator,
    regulatory_compliance_evaluator,
    tool_use_correctness_evaluator,
)


# Confidence at or above which the desk auto-accepts the classification; below it
# the item is flagged for a human analyst. The triage threshold of the use case.
ROUTE_THRESHOLD = 0.70

_LABELS = ("positive", "negative", "neutral")


# --------------- Prompts ---------------

# Mirrors the registered `usecase-sentiment-classify` template; used as a fallback
# when prompt management is unavailable so the agent never hard-depends on a fetch.
CLASSIFY_FALLBACK = (
    "You are a market-news analyst on a trading desk. Classify the sentiment of "
    "the financial text as exactly one of: positive, negative, neutral. Then give "
    "your confidence as a number between 0 and 1.\n\n"
    "Text: {{text}}\n\n"
    "Respond on one line as: <label> | <confidence>"
)


# --------------- Helpers ---------------

def _get_input(item):
    inp = item.input if hasattr(item, "input") else item.get("input", {})
    if isinstance(inp, str):
        return {"text": inp}
    return inp or {}


def _parse_label_conf(raw: str) -> tuple[str, float]:
    """Parse an LLM classification into ``(label, confidence)``, leniently.

    Handles the expected ``"<label> | <confidence>"`` line as well as off-format
    responses (bare label, prose, a percentage). On failure it defaults to a
    *low* confidence so the item routes to a human — the safe triage behavior is
    to escalate uncertainty, never to silently auto-accept a parse error.
    """
    text = (raw or "").strip().lower()

    label = next((c for c in _LABELS if c in text), None)

    confidence = None
    m = re.search(r"\d*\.\d+|\d+", text)
    if m:
        try:
            c = float(m.group(0))
            # Treat a percentage ("82%") or a whole number in (1, 100] ("82") as a
            # percent and normalize; a malformed >1 fraction ("1.5") just clamps.
            if "%" in text or (c > 1 and c == int(c) and c <= 100):
                c = c / 100.0
            confidence = max(0.0, min(1.0, c))
        except ValueError:
            confidence = None

    if label is None:
        label = "neutral"       # safe, non-actionable default
    if confidence is None:
        confidence = 0.5        # below ROUTE_THRESHOLD -> flag-for-analyst
    return label, confidence


def _extract_driver_phrase(text: str, label: str) -> str:
    """Pick the phrase most likely driving the sentiment (deterministic).

    Heuristic: prefer the sentence carrying a number, percentage, or currency
    figure (financial sentiment is usually quantitative — "rose 50.6%", "dropped
    10 percent"); otherwise the first sentence. No LLM call, so the rationale is
    reproducible and cheap.
    """
    if not text:
        return ""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]
    if not sentences:
        return text[:200]
    quantitative = [s for s in sentences if re.search(r"\d|%|€|£|\$|eur|usd|gbp", s.lower())]
    chosen = quantitative[0] if quantitative else sentences[0]
    return chosen[:200]


# --------------- The agent ---------------

def run_sentiment_triage(*, model: str, route_threshold: float = ROUTE_THRESHOLD, **opts):
    """Factory: returns a Langfuse experiment task for the Sentiment Triage use case.

    ``route_threshold`` is exposed so a demo can show the routing tool shifting
    items between auto-accept and flag-for-analyst without changing the model.
    """

    def task(*, item, **kwargs):
        inp = _get_input(item)
        text = inp.get("text", inp.get("question", ""))

        steps, tools = [], []

        # 1) CLASSIFY — label + confidence (managed prompt, lenient parse)
        classify_prompt = get_managed_prompt("usecase-sentiment-classify", CLASSIFY_FALLBACK)
        if classify_prompt is not None and hasattr(classify_prompt, "compile"):
            classify_user = classify_prompt.compile(text=text)
        else:
            classify_user = CLASSIFY_FALLBACK.replace("{{text}}", text)

        raw = traced_generation(
            name="classify", model=model, max_tokens=128,
            system="You are a precise market-news sentiment analyst.",
            user=classify_user,
        )
        steps.append("classify")
        label, confidence = _parse_label_conf(raw)

        # 2) RATIONALE — phrase driving the sentiment (deterministic span)
        with traced_span("rationale") as s:
            phrase = _extract_driver_phrase(text, label)
            steps.append("rationale")
            s.update(input={"label": label}, output={"driver_phrase": phrase})

        # 3) ROUTE — deterministic triage on confidence (the production safeguard)
        with traced_tool("route") as t:
            action = "auto-accept" if confidence >= route_threshold else "flag-for-analyst"
            tools.append("route")
            t.update(input={"confidence": confidence, "threshold": route_threshold},
                     output={"action": action})

        return AgentResult(answer=label, trajectory={
            "question_type": "sentiment",
            "steps": steps,
            "tools_used": tools,
            "operands": {"confidence": confidence},
            "citations": [phrase] if phrase else [],
            "action": action,
        }).to_output()

    return task


# --------------- Registration ---------------

# Note on the gate: only `sentiment_accuracy` can realistically vary across runs —
# the answer is just the label so `regulatory_compliance` is trivially 1.0, and
# `route` runs on every item so `tool_use_correctness` is trivially 1.0. The gate
# still certifies all three (a regression that broke routing or leaked a prohibited
# phrase into the label *would* fail), but the FAIL story for this use case is a
# weak classifier dropping `sentiment_accuracy` below 85%, not routing. Scoring
# whether the routing *action* itself was correct would need labelled
# escalate/accept ground truth, which FPB does not carry — noted as a future eval.
# Bars live in cicd/thresholds.json (see that file for the hard-vs-loose
# rationale). Gated: sentiment_accuracy (vs FPB gold), regulatory_compliance
# (hard — no prohibited phrase leaks into the label), tool_use_correctness
# (hard — the route tool must run on every item).
GATE_SENTIMENT_TRIAGE = agent_gate_thresholds("sentiment-triage")

ITEM_EVALUATORS = [
    sentiment_evaluator,
    regulatory_compliance_evaluator,
    tool_use_correctness_evaluator,
]

register_agent(
    "sentiment-triage",
    fn=run_sentiment_triage,
    gate_thresholds=GATE_SENTIMENT_TRIAGE,
    item_evaluators=ITEM_EVALUATORS,
    dataset_hint="fpb",
    description="Market Sentiment Triage — classify sentiment and route low-confidence items to a human",
)
