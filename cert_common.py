#!/usr/bin/env python3
"""
Shared certification helpers used by both the model-cert runner
(``run_certification.py``) and the use-case-cert runner
(``run_usecase_certification.py``).

Centralizing these avoids copy-paste drift between the two runners and gives a
single home for the Langfuse lifecycle plumbing:

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


REVIEW_QUEUE_NAME = "Certification Review"


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

def queue_failed_items(item_results, should_queue):
    """Route traces to the 'Certification Review' annotation queue for human review.

    Args:
        item_results: experiment item results (each with .trace_id and .evaluations).
        should_queue: callable(list_of_evaluations) -> bool, deciding per item.

    Requires the queue to exist (created by setup_annotation_queues.py).
    """
    host, auth = langfuse_creds()
    headers = {"Authorization": f"Basic {auth}", "Content-Type": "application/json"}

    # Find the queue ID
    try:
        req = urllib.request.Request(
            f"{host}/api/public/annotation-queues?limit=100",
            headers=headers,
        )
        queues = json.loads(urllib.request.urlopen(req).read()).get("data", [])
        queue_id = next(
            (q["id"] for q in queues if q["name"] == REVIEW_QUEUE_NAME), None
        )
        if not queue_id:
            print(f"  Warning: annotation queue '{REVIEW_QUEUE_NAME}' not found. "
                  f"Run setup_annotation_queues.py first.", file=sys.stderr)
            return
    except Exception as e:
        print(f"  Warning: could not list annotation queues: {e}", file=sys.stderr)
        return

    queued = 0
    for ir in item_results:
        if not getattr(ir, "trace_id", None):
            continue
        if not should_queue(ir.evaluations):
            continue
        try:
            body = json.dumps({
                "objectId": ir.trace_id,
                "objectType": "TRACE",
                "status": "PENDING",
            }).encode()
            req = urllib.request.Request(
                f"{host}/api/public/annotation-queues/{queue_id}/items",
                data=body,
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req)
            queued += 1
        except Exception as e:
            print(f"  Warning: failed to queue trace {ir.trace_id[:12]}...: {e}",
                  file=sys.stderr)

    if queued:
        print(f"\n  Queued {queued} items for human review in '{REVIEW_QUEUE_NAME}'",
              file=sys.stderr)
