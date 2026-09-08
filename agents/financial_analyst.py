#!/usr/bin/env python3
"""
Use Case 1 — 10-K Filing Analyst Agent (FinanceBench).

An analyst copilot that answers questions about SEC filings — metric extraction,
ratio computation, year-over-year changes — with citations to line items, and a
*calculator tool* so the arithmetic is grounded rather than guessed.

Trace per dataset item (nesting verified against run_experiment — see
scripts/spike_span_nesting.py):

    usecase:10k-analyst                      trace
    ├── plan               (generation)  classify; decide if calc is needed; list line items
    ├── retrieve-evidence  (span)
    │     └── extract-operands (generation)  read excerpts -> arithmetic expr + operands + citations
    ├── calculate          (tool)         deterministic safe_eval of the expression (numerical Qs)
    └── compose-answer     (generation)  grounded answer + citations

Design note — prompt ownership:
  The plan and extract steps emit JSON we parse, so their prompts are code-owned
  (a UI edit that breaks the JSON would break the agent). The final compose step
  is free-form, so it is fetched from Langfuse prompt management
  (usecase-10k-analyst-compose) and is editable/versionable in the UI. This keeps
  parsing stable while still demonstrating the prompt lifecycle.

Reference implementation for GitHub issue #9. Claude-only for now (the agent calls
Claude via the native Anthropic SDK through traced_generation).
"""

import ast
import json
import operator
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
    numerical_accuracy_evaluator,
    groundedness_evaluator,
    regulatory_compliance_evaluator,
    tool_use_correctness_evaluator,
)


# --------------- Safe arithmetic (the calculator tool) ---------------

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def safe_eval(expr: str) -> float:
    """Evaluate a pure arithmetic expression (+ - * / and parens) with no names,
    calls, or attribute access. Never uses Python eval().

    Exponentiation (``**``) is intentionally unsupported: it is not needed for the
    ratios/averages/YoY changes the agent computes (and the extract prompt only
    emits + - * / ( )), while ``9**9**9`` would compute a multi-gigabyte integer
    and hang the worker before the calculate step's try/except could catch it.
    """
    def _ev(node):
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("non-numeric constant")
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_ev(node.left), _ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](_ev(node.operand))
        raise ValueError(f"unsupported expression: {ast.dump(node)}")

    return _ev(ast.parse(expr.strip(), mode="eval"))


# --------------- JSON parsing (lenient) ---------------

def _parse_json(text: str, default: dict) -> dict:
    """Parse a JSON object from an LLM response, tolerating markdown fences and
    surrounding prose. Falls back to `default` on failure.

    Always returns a dict: if the model emits valid-but-non-object JSON (e.g. a
    top-level array or scalar), that is treated as a parse failure so callers can
    safely ``.get(...)`` the result without an AttributeError.
    """
    if not text:
        return dict(default)
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return dict(default)


# --------------- Prompts ---------------

# Code-owned (parsed) — see module docstring.
PLAN_SYS = (
    "You are a financial analyst planning how to answer a question about an SEC "
    "filing. The question's reasoning type is: {qtype}. Decide whether answering "
    "requires arithmetic (a ratio, an average, a year-over-year change) or is a "
    "direct extraction of a single reported figure. Identify the exact line items "
    "you will need.\n\n"
    'Respond with ONLY a JSON object: {{"needs_calc": <true|false>, '
    '"line_items": [<strings>], "approach": "<one sentence>"}}'
)
EXTRACT_SYS = (
    "You are a financial analyst extracting figures from SEC filing excerpts. Use "
    "ONLY the excerpts provided — never invent numbers.\n"
    "- If the question needs a calculation, return a single arithmetic expression "
    "that computes the answer using ONLY numbers and the operators + - * / ( ) "
    "(no variables, no words). Round nothing; let the calculator do the math.\n"
    "- If it is a direct extraction, put the extracted figure in extracted_value.\n"
    "Record the operands you used and cite the line items.\n\n"
    'Respond with ONLY a JSON object: {{"expression": "<arithmetic or empty>", '
    '"operands": {{<name>: <number>}}, "extracted_value": "<string or empty>", '
    '"citations": [<line item strings>]}}'
)
# Free-form (not parsed) — fetched from prompt management, editable in the UI.
COMPOSE_FALLBACK = (
    "You are a financial analyst writing the final answer. Use ONLY the operands "
    "and computed value provided; never invent numbers. State the answer precisely "
    "in the units/format the question asks for, and cite the line items. Do not give "
    "buy/sell advice or use promotional language.\n\n"
    "--- Question ---\n{{question}}\n\n"
    "--- Operands ---\n{{operands}}\n\n"
    "--- Computed value (from the calculator tool, if any) ---\n{{computed}}\n\n"
    "--- Citations ---\n{{citations}}"
)


# --------------- Helpers ---------------

def _get_input(item):
    return item.input if hasattr(item, "input") else item.get("input", {})


def _get_metadata(item):
    if hasattr(item, "metadata"):
        return item.metadata or {}
    return item.get("metadata", {}) or {}


def _evidence_block(evidence) -> str:
    parts = []
    for i, ev in enumerate(evidence or [], 1):
        if ev:
            parts.append(f"--- Source Document Excerpt {i} ---\n{ev}")
    return "\n\n".join(parts)


# --------------- The agent ---------------

def run_10k_analyst(*, model: str, **opts):
    """Factory: returns a Langfuse experiment task for the 10-K Analyst use case."""

    def task(*, item, **kwargs):
        inp = _get_input(item)
        if isinstance(inp, str):
            inp = {"question": inp}
        meta = _get_metadata(item)
        question = inp.get("question", inp.get("text", ""))
        evidence = inp.get("evidence", [])
        qtype = meta.get("question_reasoning", "") or meta.get("question_type", "")

        steps, tools, operands, citations = [], [], {}, []

        # 1) PLAN — classify + decide tool need + list line items
        plan_raw = traced_generation(
            name="plan", model=model, max_tokens=512,
            system=PLAN_SYS.format(qtype=qtype or "unspecified"),
            user=f"Question: {question}",
        )
        steps.append("plan")
        plan = _parse_json(plan_raw, {"needs_calc": False, "line_items": [], "approach": ""})
        needs_calc = bool(plan.get("needs_calc"))

        # 2) RETRIEVE-EVIDENCE — extract operands / expression from the filing
        with traced_span("retrieve-evidence") as s:
            extract_raw = traced_generation(
                name="extract-operands", model=model, max_tokens=1024,
                system=EXTRACT_SYS,
                user=(f"Question: {question}\n\nApproach: {plan.get('approach', '')}\n\n"
                      f"Line items to find: {plan.get('line_items', [])}\n\n"
                      f"{_evidence_block(evidence)}"),
            )
            extracted = _parse_json(
                extract_raw,
                {"expression": "", "operands": {}, "extracted_value": "", "citations": []},
            )
            operands = extracted.get("operands", {}) or {}
            citations = extracted.get("citations", []) or []
            expression = (extracted.get("expression") or "").strip()
            extracted_value = extracted.get("extracted_value", "")
            steps.append("retrieve-evidence")
            s.update(input={"approach": plan.get("approach", "")},
                     output={"operands": operands, "expression": expression,
                             "citations": citations})

        # 3) CALCULATE — deterministic arithmetic (only when an expression exists)
        computed = None
        if needs_calc and expression:
            with traced_tool("calculate") as t:
                try:
                    computed = safe_eval(expression)
                    tools.append("calculate")
                    t.update(input={"expression": expression},
                             output={"result": computed})
                except Exception as e:
                    t.update(input={"expression": expression},
                             output={"error": str(e)}, level="ERROR")
                    computed = None

        # 4) COMPOSE-ANSWER — grounded answer + citations (managed prompt)
        compose_prompt = get_managed_prompt("usecase-10k-analyst-compose", COMPOSE_FALLBACK)
        computed_str = "" if computed is None else str(computed)
        if compose_prompt is not None and hasattr(compose_prompt, "compile"):
            compose_user = compose_prompt.compile(
                question=question, operands=json.dumps(operands),
                computed=computed_str or (extracted_value or "n/a"),
                citations="\n".join(citations),
            )
            compose_sys = "You are a precise financial analyst."
        else:
            compose_user = COMPOSE_FALLBACK.replace("{{question}}", question) \
                .replace("{{operands}}", json.dumps(operands)) \
                .replace("{{computed}}", computed_str or (extracted_value or "n/a")) \
                .replace("{{citations}}", "\n".join(citations))
            compose_sys = "You are a precise financial analyst."

        answer = traced_generation(
            name="compose-answer", model=model, max_tokens=1024,
            system=compose_sys, user=compose_user,
        )
        steps.append("compose-answer")

        return AgentResult(answer=answer, trajectory={
            "question_type": qtype,
            "steps": steps,
            "tools_used": tools,
            "operands": operands,
            "citations": citations,
        }).to_output()

    return task


# --------------- Registration ---------------

# The gate bars live in cicd/thresholds.json, not here — loosening one should
# show up as a reviewable diff in a PR. Dimensions gated: numerical_accuracy
# (correctness), groundedness (no hallucinated numbers, LLM judge),
# regulatory_compliance (hard 1.00), tool_use_correctness (numerical Qs actually
# used the calculator). Raises at import if the entry is missing.
GATE_10K_ANALYST = agent_gate_thresholds("10k-analyst")

# Note: exact_match is intentionally excluded — strict string containment is
# near-useless for numerical/derived answers (it scored ~40% while the gate
# passed, prompting "why?" on the breakdown page). numerical_accuracy covers
# correctness; groundedness covers faithfulness.
ITEM_EVALUATORS = [
    numerical_accuracy_evaluator,
    groundedness_evaluator,
    regulatory_compliance_evaluator,
    tool_use_correctness_evaluator,
]

register_agent(
    "10k-analyst",
    fn=run_10k_analyst,
    gate_thresholds=GATE_10K_ANALYST,
    item_evaluators=ITEM_EVALUATORS,
    dataset_hint="financebench",
    description="10-K Filing Analyst — grounded SEC-filing QA with a calculator tool",
)
