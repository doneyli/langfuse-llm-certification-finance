"""Use-case (agent) certification package.

Exposes the shared foundation (registry + span helpers + structured result) and
triggers each agent module to self-register on import. The concrete agents are
delivered in separate issues (#9 10k-analyst, #10 sentiment-triage,
#11 advisory-draft); their absence is tolerated so the foundation works
standalone.
"""

from agents.base import (
    AGENT_REGISTRY,
    AgentResult,
    register_agent,
    traced_generation,
    traced_span,
    traced_tool,
)

# Import agent modules so they self-register. If an agent module file does not
# exist yet (it lands in a later issue), skip it — but re-raise if the failure is
# a genuine missing transitive dependency *inside* an agent module, so real bugs
# are not silently swallowed once the agents are implemented.
for _mod in (
    "agents.financial_analyst",
    "agents.sentiment_triage",
    "agents.advisory_draft",
):
    try:
        __import__(_mod)
    except ModuleNotFoundError as _e:
        if _e.name != _mod:
            raise

__all__ = [
    "AGENT_REGISTRY",
    "AgentResult",
    "register_agent",
    "traced_generation",
    "traced_span",
    "traced_tool",
]
