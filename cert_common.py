#!/usr/bin/env python3
"""
Shared certification helpers used by both the model-cert runner
(``run_certification.py``) and the use-case-cert runner
(``run_usecase_certification.py``).

Centralizing these avoids copy-paste drift between the two runners and gives a
single home for the Langfuse lifecycle plumbing:

  agent_gate_thresholds   - the gate bars for one use case, read from
                            cicd/thresholds.json (the quality bar as code)
  model_gate_threshold    - the model-cert pass bar, same file
  langfuse_creds          - host + basic-auth header from env
  get_managed_prompt      - fetch a Langfuse-managed prompt (production label),
                            falling back to a hardcoded template (prompt mgmt)
  persist_run_evaluations - write run-level scores back to Langfuse (scores)
  queue_failed_items      - route low-scoring traces to an annotation queue
                            (human review)
"""

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path


REVIEW_QUEUE_NAME = "Certification Review"

THRESHOLDS_PATH = Path(__file__).resolve().parent / "cicd" / "thresholds.json"


# --------------- The quality bar (thresholds as code) ---------------

_thresholds_cache: dict = {}


def load_thresholds(path=None) -> dict:
    """Read and cache cicd/thresholds.json — the single source of truth for gates.

    Keys starting with '_' are commentary and are dropped. Read at import time by
    every agent module, so the parsed result is cached per resolved path. Pass an
    explicit ``path`` to read a different file (tests use a stub).
    """
    key = str(Path(path).resolve()) if path else str(THRESHOLDS_PATH)
    if key not in _thresholds_cache:
        try:
            raw = json.loads(Path(key).read_text())
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Gate thresholds file missing: {key}\n"
                f"This file IS the quality bar — the gates cannot run without it. "
                f"Restore it from git rather than hardcoding thresholds back into "
                f"the agent modules."
            ) from None
        _thresholds_cache[key] = {k: v for k, v in raw.items()
                                  if not k.startswith("_")}
    return _thresholds_cache[key]


def agent_gate_thresholds(use_case: str, *, path=None) -> dict:
    """Return {score_name: min_threshold} for one use case.

    Raises on a missing or empty entry, deliberately. ``usecase_certification_gate``
    is ``all()`` over the dimensions it is handed, and ``all()`` over an empty dict
    is ``True`` — so an agent registered with no thresholds would report PASSED
    unconditionally. Failing loudly at import time is the only safe behavior: a
    gate that cannot find its bar must not run, rather than pass everything.
    """
    gates = load_thresholds(path).get("agent_gates", {})
    thresholds = gates.get(use_case)
    if not thresholds:
        known = ", ".join(sorted(gates)) or "(none)"
        raise KeyError(
            f"No gate thresholds for use case '{use_case}' in "
            f"{path or THRESHOLDS_PATH}. Known use cases: {known}. "
            f"An empty gate certifies everything as PASSED, so this is fatal "
            f"rather than a default."
        )
    return {name: float(v) for name, v in thresholds.items()}


def model_gate_threshold(*, path=None) -> float:
    """Return the model-certification pass bar (run_certification.py --threshold)."""
    model_gate = load_thresholds(path).get("model_gate", {})
    threshold = model_gate.get("default_threshold")
    if threshold is None:
        raise KeyError(
            f"No model_gate.default_threshold in {path or THRESHOLDS_PATH}."
        )
    return float(threshold)


# --------------- Credentials ---------------

def langfuse_creds():
    """Return (host, basic_auth_header) from LANGFUSE_* env vars."""
    host = os.getenv("LANGFUSE_BASE_URL",
                     os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return host, auth


# --------------- Prompt management ---------------

def get_managed_prompt(name: str, fallback: str):
    """Fetch a prompt from Langfuse prompt management (``production`` label).

    Returns the Langfuse prompt object (call ``.compile(**vars)`` on it) or, if
    Langfuse is unavailable, ``None`` so the caller can use ``fallback`` directly.
    Mirrors the behavior in ``run_certification.py`` so agents share one prompt
    lifecycle: edit/version/promote in the Langfuse UI, no code change.
    """
    try:
        from langfuse import get_client
        return get_client().get_prompt(name, label="production", fallback=fallback)
    except Exception:
        return None


# --------------- Run-level score persistence ---------------

def persist_run_evaluations(result):
    """Persist run-level evaluations as scores on the first experiment trace.

    The Langfuse SDK computes ``run_evaluators`` locally but does not store them.
    We POST them via the REST API, attaching to the first experiment trace so they
    appear in the Langfuse UI under that trace's scores.
    """
    if not (getattr(result, "run_evaluations", None) and
            getattr(result, "item_results", None)):
        return

    first_trace_id = None
    for ir in result.item_results:
        if getattr(ir, "trace_id", None):
            first_trace_id = ir.trace_id
            break
    if not first_trace_id:
        return

    host, auth = langfuse_creds()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

    for ev in result.run_evaluations:
        if ev.value is None:
            continue
        try:
            body = json.dumps({
                "traceId": first_trace_id,
                "name": ev.name,
                "value": ev.value,
                "comment": ev.comment or "",
                "dataType": "NUMERIC",
            }).encode()
            req = urllib.request.Request(
                f"{host}/api/public/scores",
                data=body,
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req)
        except Exception as e:
            print(f"  Warning: failed to persist {ev.name}: {e}", file=sys.stderr)


# --------------- Annotation queue routing ---------------

def _find_review_queue_id(host, auth, headers):
    """Return the 'Certification Review' annotation-queue id, or None (+warning)."""
    try:
        req = urllib.request.Request(
            f"{host}/api/public/annotation-queues?limit=100", headers=headers)
        queues = json.loads(urllib.request.urlopen(req).read()).get("data", [])
        queue_id = next(
            (q["id"] for q in queues if q["name"] == REVIEW_QUEUE_NAME), None)
        if not queue_id:
            print(f"  Warning: annotation queue '{REVIEW_QUEUE_NAME}' not found. "
                  f"Run setup_annotation_queues.py first.", file=sys.stderr)
        return queue_id
    except Exception as e:
        print(f"  Warning: could not list annotation queues: {e}", file=sys.stderr)
        return None


def queue_trace_ids(trace_ids):
    """Add trace ids to the 'Certification Review' annotation queue for human review.

    Deduplicates the input, skips falsy ids, and returns the number queued. This is
    the shared primitive behind both offline-cert failure routing (queue_failed_items)
    and live production-monitoring routing (monitor_production.py --queue-violations),
    so a flagged trace reaches the same human-review inbox regardless of source.

    Requires the queue to exist (created by setup_annotation_queues.py).
    """
    unique = [t for t in dict.fromkeys(trace_ids) if t]
    if not unique:
        return 0
    host, auth = langfuse_creds()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    queue_id = _find_review_queue_id(host, auth, headers)
    if not queue_id:
        return 0

    queued = 0
    for tid in unique:
        try:
            body = json.dumps({
                "objectId": tid, "objectType": "TRACE", "status": "PENDING",
            }).encode()
            req = urllib.request.Request(
                f"{host}/api/public/annotation-queues/{queue_id}/items",
                data=body, headers=headers, method="POST")
            urllib.request.urlopen(req)
            queued += 1
        except Exception as e:
            print(f"  Warning: failed to queue trace {tid[:12]}...: {e}",
                  file=sys.stderr)

    if queued:
        print(f"\n  Queued {queued} trace(s) for human review in '{REVIEW_QUEUE_NAME}'",
              file=sys.stderr)
    return queued


def queue_failed_items(item_results, should_queue):
    """Route failing experiment items to the review queue (offline cert runs).

    Args:
        item_results: experiment item results (each with .trace_id and .evaluations).
        should_queue: callable(list_of_evaluations) -> bool, deciding per item.
    """
    trace_ids = [ir.trace_id for ir in item_results
                 if getattr(ir, "trace_id", None) and should_queue(ir.evaluations)]
    return queue_trace_ids(trace_ids)


def fetch_review_queue_trace_ids(limit=100):
    """Return the trace ids of TRACE items currently in the review queue.

    Used to promote human-reviewed production traces into a golden dataset
    (see promote_trace_to_dataset.py --from-queue) — the queue → dataset step
    that closes the observation → development feedback edge.
    """
    host, auth = langfuse_creds()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}
    queue_id = _find_review_queue_id(host, auth, headers)
    if not queue_id:
        return []
    try:
        req = urllib.request.Request(
            f"{host}/api/public/annotation-queues/{queue_id}/items?limit={limit}",
            headers=headers)
        items = json.loads(urllib.request.urlopen(req).read()).get("data", [])
        return [it["objectId"] for it in items
                if it.get("objectType") == "TRACE" and it.get("objectId")]
    except Exception as e:
        print(f"  Warning: could not fetch queue items: {e}", file=sys.stderr)
        return []
