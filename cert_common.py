#!/usr/bin/env python3
"""
Shared certification helpers used by both the model-cert runner
(``run_certification.py``) and the use-case-cert runner
(``run_usecase_certification.py``).

Centralizing these avoids copy-paste drift between the two runners and gives a
single home for the Langfuse lifecycle plumbing:

  agent_gate_thresholds   - the required gate bars for one use case, read from
                            cicd/thresholds.json (the quality bar as code)
  agent_advisory_thresholds - the advisory (non-blocking) bars, same file
  model_gate_threshold    - the model-cert pass bar, same file
  recorded_gate           - the bars a past run was actually judged by, read
                            from its own metadata (portal + evidence pack)
  recorded_advisory       - the advisory bars a past run reported, same source;
                            never part of the verdict
  bar_verdict             - one score's bar, whether it cleared, and whether
                            that bar was advisory (portal + evidence pack)
  langfuse_creds          - host + basic-auth header from env
  get_managed_prompt      - fetch a Langfuse-managed prompt (production label),
                            falling back to a hardcoded template (prompt mgmt)
  api_get / paginate_cursor - the Langfuse v4 read API (cursor pagination,
                            required fromStartTime) shared by portal, exporter,
                            monitor and promoter
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


_BAR_KEYS = {"threshold", "required"}


def _parse_bar(use_case: str, name: str, value, source) -> tuple[float, bool]:
    """One ``agent_gates`` entry as ``(bar, required)``.

    Two shapes are accepted. A bare number is a required bar, which is what
    every entry was before the advisory tier existed, so older files mean
    exactly what they meant. ``{"threshold": x, "required": false}`` is an
    advisory bar; ``required`` defaults to true when omitted. Anything else
    raises: a typo such as ``"requried": false`` must not be read as a bar.
    """
    where = f"agent_gates.{use_case}.{name} in {source}"
    required = True
    if isinstance(value, dict):
        unknown = set(value) - _BAR_KEYS
        if unknown or "threshold" not in value:
            raise ValueError(
                f"{where}: expected a number or "
                f'{{"threshold": <0..1>, "required": <bool>}}, got {value!r}')
        required = value.get("required", True)
        if not isinstance(required, bool):
            raise ValueError(f"{where}: 'required' must be true or false, "
                             f"got {required!r}")
        value = value["threshold"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where}: threshold must be a number, got {value!r}")
    return float(value), required


def agent_gate_bars(use_case: str, *, path=None) -> tuple[dict, dict]:
    """Return ``(required, advisory)`` bars for one use case, each {name: bar}.

    Required bars decide the verdict: ``usecase_certification_gate`` is ``all()``
    over them. Advisory bars are measured and reported the same way but never
    block a release.

    Raises on a missing or empty entry, and on an entry with no required bar,
    deliberately. ``all()`` over an empty dict is ``True``, so an agent with
    nothing required would report PASSED unconditionally, whatever its
    advisory bars say. Failing loudly at import time is the only safe
    behavior: a gate that cannot find a bar that blocks must not run, rather
    than pass everything.
    """
    source = path or THRESHOLDS_PATH
    gates = load_thresholds(path).get("agent_gates", {})
    entry = gates.get(use_case)
    if not entry:
        known = ", ".join(sorted(gates)) or "(none)"
        raise KeyError(
            f"No gate thresholds for use case '{use_case}' in {source}. "
            f"Known use cases: {known}. An empty gate certifies everything as "
            f"PASSED, so this is fatal rather than a default."
        )
    required, advisory = {}, {}
    for name, value in entry.items():
        bar, is_required = _parse_bar(use_case, name, value, source)
        (required if is_required else advisory)[name] = bar
    if not required:
        raise KeyError(
            f"No required gate thresholds for use case '{use_case}' in "
            f"{source}: every bar is advisory ({', '.join(sorted(advisory))}). "
            f"Advisory bars never block, so this gate would certify everything "
            f"as PASSED. Mark at least one bar required."
        )
    return required, advisory


def agent_gate_thresholds(use_case: str, *, path=None) -> dict:
    """Return the REQUIRED bars for one use case, {score_name: min_threshold}.

    These are the bars the gate's verdict is ``all()`` over. See
    ``agent_gate_bars`` for why a use case with none raises.
    """
    return agent_gate_bars(use_case, path=path)[0]


def agent_advisory_thresholds(use_case: str, *, path=None) -> dict:
    """Return the ADVISORY bars for one use case, {score_name: min_threshold}.

    Empty when the use case has none. Measured, reported in the gate's comment
    and recorded in run metadata, but excluded from the verdict.
    """
    return agent_gate_bars(use_case, path=path)[1]


def model_gate_threshold(*, path=None) -> float:
    """Return the model-certification pass bar (run_certification.py --threshold)."""
    model_gate = load_thresholds(path).get("model_gate", {})
    threshold = model_gate.get("default_threshold")
    if threshold is None:
        raise KeyError(
            f"No model_gate.default_threshold in {path or THRESHOLDS_PATH}."
        )
    return float(threshold)


# --------------- Reading the gate a run recorded ---------------

# The only scores the model gate ever judges against its scalar threshold — see
# run_certification.select_evaluators(). Used to name the judged score for runs
# recorded before that runner started writing `metadata.gate`.
MODEL_GATE_SCORES = ("numerical_accuracy", "sentiment_accuracy")


def _as_bar(value):
    """A recorded bar as a float; None for anything else (incl. bools).

    Accepts JSON numbers and numeric strings: SDK v5 experiment metadata comes
    back from the /experiments endpoint with every value stringified
    (``"threshold": "0.85"``).
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    return float(value)


def recorded_gate(meta, score_names=()) -> dict | None:
    """Return the gate a dataset run was judged by, as {score_name: bar}, or None.

    Read from the run's own metadata — never from cicd/thresholds.json — so a
    historical run reports the bar that was in force when it ran, not today's.
    Three producers record a gate, in two shapes:

    * run_usecase_certification.py (agent gate) writes ``gate_thresholds``: one
      bar per dimension, every one of which must clear.
    * tests/test_certification.py (live gate) writes ``threshold`` plus
      ``gate``, the one score that threshold judged.
    * run_certification.py (model gate) writes ``threshold`` and ``gate``. Runs
      recorded before it wrote ``gate`` are resolved from ``score_names``: the
      judged score is whichever of MODEL_GATE_SCORES the run carries, which is
      exactly the one select_evaluators() picks.

    A threshold is a bar only for the score it judged, so it is never applied
    to any other score — an evaluator the gate did not judge has no bar. And an
    agent run whose recorded gate holds no usable bars returns None rather than
    falling back to a scalar: an empty agent gate certifies everything (see
    agent_gate_thresholds), so no bar was actually enforced.
    """
    meta = normalize_run_metadata(meta)
    if meta.get("gate_thresholds") is not None:
        raw = meta["gate_thresholds"]
        bars = ({name: _as_bar(v) for name, v in raw.items()}
                if isinstance(raw, dict) else {})
        bars = {name: bar for name, bar in bars.items() if bar is not None}
        return bars or None

    threshold = _as_bar(meta.get("threshold"))
    if threshold is None:
        return None
    judged = meta.get("gate")
    if not isinstance(judged, str) or not judged:
        present = set(score_names)
        judged = next((s for s in MODEL_GATE_SCORES if s in present), None)
    return {judged: threshold} if judged else None


def describe_gate(gate: dict | None) -> str:
    """One line naming every bar, e.g. 'groundedness ≥ 80% · completeness ≥ 70%'.

    A multi-dimension gate is prefixed 'all must clear' because that is the rule
    the agent gate applies; a gate that recorded no bar says so rather than
    printing a number nothing was judged against.
    """
    if not gate:
        return "— (no bar recorded for this run)"
    bars = " · ".join(f"{name} ≥ {bar:.0%}" for name, bar in sorted(gate.items()))
    return f"all must clear: {bars}" if len(gate) > 1 else bars


def recorded_advisory(meta) -> dict | None:
    """Return the advisory bars a dataset run reported, as {score_name: bar}, or None.

    run_usecase_certification.py records them as ``advisory_thresholds``, apart
    from ``gate_thresholds`` on purpose: they were measured and reported but
    never decided the verdict, so they are never part of ``recorded_gate`` or a
    verdict replay. A name that is also a required bar is dropped, since the
    required bar is the one that judged it. Runs recorded before the advisory
    tier existed return None.
    """
    meta = normalize_run_metadata(meta)
    raw = meta.get("advisory_thresholds")
    if not isinstance(raw, dict):
        return None
    required = recorded_gate(meta) or {}
    bars = {name: _as_bar(v) for name, v in raw.items() if name not in required}
    bars = {name: bar for name, bar in bars.items() if bar is not None}
    return bars or None


def describe_advisory(advisory: dict | None) -> str:
    """One line naming every advisory bar, e.g. 'completeness ≥ 70% (never blocks)'."""
    if not advisory:
        return ""
    bars = " · ".join(f"{name} ≥ {bar:.0%}" for name, bar in sorted(advisory.items()))
    return f"{bars} (reported, never blocks)"


def bar_verdict(name: str, mean: float, required: dict, advisory: dict) -> dict:
    """The bar one score was held to, as {"bar", "cleared", "advisory"}.

    ``mean`` must be the unrounded mean the gate compared. A required bar wins
    over an advisory one of the same name. An advisory bar reports whether it
    was met but never changes a verdict, which is why callers get the
    ``advisory`` flag instead of having to cross-reference two dicts. A score
    with neither bar gets ``bar: None, cleared: None``.
    """
    if name in required:
        bar, is_advisory = required[name], False
    elif name in advisory:
        bar, is_advisory = advisory[name], True
    else:
        return {"bar": None, "cleared": None, "advisory": False}
    return {"bar": bar, "cleared": mean >= bar, "advisory": is_advisory}


# --------------- Credentials ---------------

def langfuse_creds():
    """Return (host, basic_auth_header) from LANGFUSE_* env vars."""
    host = os.getenv("LANGFUSE_BASE_URL",
                     os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return host, auth


# --------------- Langfuse v4 read API (cursor pagination) ---------------
#
# Langfuse Cloud removed the legacy read endpoints (/traces, /scores,
# /datasets/{name}/runs, /dataset-run-items) on 2026-11-16. Their replacements —
# /experiments, /experiment-items, /v2/observations, /v3/scores — share one
# shape: cursor pagination via ``meta.cursor``, ``limit <= 100``, and (for the
# experiment and observation endpoints) a required ``fromStartTime``.

PAGE_SIZE = 100
MAX_PAGES = 100  # circuit breaker: 10k rows max per paginated call

# How far back the portal / exporter look for experiments by default. The v4
# endpoints require a lower time bound; two years covers every run this demo
# has produced. Override with PORTAL_LOOKBACK_DAYS.
DEFAULT_LOOKBACK_DAYS = 730


def iso_utc(dt=None, *, days_ago=None, hours_ago=None) -> str:
    """ISO 8601 UTC timestamp (``...Z``) for ``fromStartTime``/``toStartTime``.

    ``dt`` defaults to now; ``days_ago`` / ``hours_ago`` shift it back.
    """
    from datetime import datetime, timedelta, timezone
    dt = dt or datetime.now(timezone.utc)
    if days_ago:
        dt -= timedelta(days=days_ago)
    if hours_ago:
        dt -= timedelta(hours=hours_ago)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def lookback_from_env() -> str:
    """``fromStartTime`` for experiment listings, from PORTAL_LOOKBACK_DAYS."""
    try:
        days = float(os.getenv("PORTAL_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))
    except ValueError:
        days = DEFAULT_LOOKBACK_DAYS
    return iso_utc(days_ago=days)


def api_get(path, params=None, *, host=None, auth=None) -> dict:
    """GET a Langfuse public API path (``/api/public/...``) and decode JSON.

    ``params`` values that are lists/dicts are JSON-encoded (the ``filter``
    parameter of /v2/observations takes a JSON array); ``None`` values are
    dropped.
    """
    import urllib.parse
    if host is None or auth is None:
        host, auth = langfuse_creds()
    query = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, dict)):
            value = json.dumps(value)
        elif isinstance(value, bool):
            value = "true" if value else "false"
        query[key] = value
    url = f"{host}{path}"
    if query:
        url += ("&" if "?" in path else "?") + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    return json.loads(urllib.request.urlopen(req).read())


def paginate_cursor(path, params=None, *, host=None, auth=None,
                    max_pages=MAX_PAGES, limit=PAGE_SIZE) -> list:
    """Fetch every page of a cursor-paginated v4 list endpoint.

    Follows ``meta.cursor`` until it is absent. Returns the concatenated
    ``data`` rows.
    """
    params = dict(params or {})
    params.setdefault("limit", limit)
    out = []
    for _ in range(max_pages):
        resp = api_get(path, params, host=host, auth=auth)
        out.extend(resp.get("data") or [])
        cursor = (resp.get("meta") or {}).get("cursor")
        if not cursor:
            break
        params["cursor"] = cursor
    return out


def parse_json_field(value):
    """Decode a v4 ``input``/``output``/``expectedOutput`` field.

    The v4 read endpoints return IO as raw strings; JSON-shaped ones are
    decoded, anything else is returned as-is.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in ("{", "[", '"'):
            try:
                return json.loads(stripped)
            except ValueError:
                return value
    return value


def score_value(score):
    """A v3/v4 score's value as a float (BOOLEAN ``true/false`` -> 1.0/0.0).

    Returns None for missing or non-numeric (categorical / text) values.
    """
    value = (score or {}).get("value")
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "false"):
            return 1.0 if lowered == "true" else 0.0
        try:
            return float(lowered)
        except ValueError:
            return None
    return None


def normalize_run_metadata(meta) -> dict:
    """Experiment metadata as recorded, with JSON-string values decoded.

    SDK v5 records experiment metadata as OTel span attributes, and the
    /experiments endpoint returns it flattened with every leaf stringified:
    ``{"gate_thresholds": {"groundedness": 0.8}, "threshold": 0.85}`` comes back
    as ``{"gate_thresholds.groundedness": "0.8", "threshold": "0.85"}``
    (verified live, scripts/spike_span_nesting.py). Dotted keys are nested
    again here; numeric strings are left as strings — ``recorded_gate`` parses
    bars itself. Legacy (SDK 3.x) runs return ``{}``.
    """
    if isinstance(meta, str):
        meta = parse_json_field(meta)
    if not isinstance(meta, dict):
        return {}
    out = {}
    for key, value in meta.items():
        if isinstance(value, str) and value.strip()[:1] in ("{", "["):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        if isinstance(key, str) and "." in key:
            head, _, rest = key.partition(".")
            nested = out.get(head)
            if not isinstance(nested, dict):
                nested = {}
                out[head] = nested
            nested.update(normalize_run_metadata({rest: value}))
        elif isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


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
