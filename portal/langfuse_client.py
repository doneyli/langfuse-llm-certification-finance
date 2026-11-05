"""
Langfuse data layer for the Certification Portal.

Fetches experiment data from Langfuse (v4 read API: /experiments and
/experiment-items), aggregates scores, and caches results.
"""

import logging
import os
import urllib.parse

from cachetools import TTLCache

from cert_common import (
    MAX_PAGES,
    PAGE_SIZE,
    api_get,
    langfuse_creds,
    lookback_from_env,
    normalize_run_metadata,
    paginate_cursor,
    parse_json_field,
    recorded_gate,
    score_value,
)


logger = logging.getLogger(__name__)

_cache = TTLCache(maxsize=64, ttl=60)

# Default prefix for dataset discovery (override with PORTAL_DATASET_PREFIX,
# or pin an explicit list with PORTAL_DATASETS="name1,name2,...").
DEFAULT_DATASET_PREFIX = "certification/"

# Last-resort dataset list, used only when dataset discovery via the Langfuse
# API fails — the portal degrades to the historically known slugs instead of
# erroring out.
FALLBACK_DATASETS = [
    "certification/financebench-sample",
    "certification/fpb-sample",
    "certification/financebench-v1",
    "certification/fpb-v1",
]

# Run-level score names that must not be counted as per-item scores.
# Any `avg_*` score is also run-level: the SDK writes run_evaluators results
# onto the experiment itself (GET /experiments?fields=scores).
RUN_LEVEL_SCORES = {"certification_result"}

# Use-case slugs the agent runner prefixes run names with (see
# run_usecase_certification.KNOWN_USE_CASES), and the dataset tokens run names
# carry — used only to recover a row key for runs recorded without metadata.
KNOWN_USE_CASES = ("10k-analyst", "sentiment-triage", "advisory-draft")
DATASET_TOKENS = ("financebench", "fpb", "advisory")

# Runs named with these prefixes are disposable (scripts/spike_span_nesting.py)
# and never shown.
HIDDEN_RUN_PREFIXES = ("spike-",)

# Fallback chain for a row's "primary score": the first of these avg_* scores
# present on a run wins; otherwise the first other avg_* score (alphabetical);
# otherwise null.
PRIMARY_SCORE_CHAIN = [
    "avg_numerical_accuracy",
    "avg_sentiment_accuracy",
    "avg_groundedness",
    "avg_exact_match",
    "avg_completeness",
]


def resolve_run_thresholds(meta, primary_score_name=None, score_names=()):
    """Resolve the bar(s) a dataset run was actually judged against, for display.

    The recorded gate comes from ``cert_common.recorded_gate`` — the one rule the
    portal and the evidence pack (export_results.py) share — which reads the
    run's own metadata so a historical run shows the bar in force when it ran.

    Returns ``(threshold, gate_thresholds)``:
      threshold        the bar ``primary_score_name`` (e.g. ``avg_groundedness``)
                       was judged against, or None if the gate did not judge it.
                       With no primary score, a model gate's single bar; an
                       agent gate has no single bar, so None.
      gate_thresholds  the per-dimension dict for agent runs, else None.
    ``score_names`` are the run's (un-prefixed) score names, used to name the
    judged score of a model run recorded before its runner wrote ``gate``.
    """
    gate = recorded_gate(meta, score_names)
    if not gate:
        return None, None
    is_agent = normalize_run_metadata(meta).get("gate_thresholds") is not None
    if primary_score_name is not None:
        threshold = gate.get(primary_score_name.removeprefix("avg_"))
    else:
        threshold = None if is_agent else next(iter(gate.values()))
    return threshold, (gate if is_agent else None)


def replay_gate(gate, means):
    """Re-derive a gate's verdict from a run's per-score means.

    Fallback for runs that predate the persisted ``certification_result``
    score; live runs always read that score instead. ``means`` must be the
    *unrounded* means — the gate compared those, and a rounded 0.9996 would
    clear a 1.00 bar the gate failed. Mirrors
    ``evaluators.usecase_certification_gate``: every bar must clear, and a score
    with no values cannot certify.
    """
    cleared = all(
        dim in means and means[dim] >= bar for dim, bar in gate.items()
    )
    return "PASSED" if cleared else "FAILED"


def _run_score_names(cert):
    """Un-prefixed names of a run's run-level ``avg_*`` scores."""
    return {name.removeprefix("avg_") for name in cert if name.startswith("avg_")}


def summarize_run(score_totals, meta, cert_value=None):
    """Aggregate a run's per-item scores and judge them against its recorded gate.

    ``score_totals`` maps score name -> item values. Returns ``aggregates``
    (per score: display stats plus the ``bar`` it was judged against and
    whether it ``cleared`` it), ``threshold`` and ``gate_thresholds`` (see
    resolve_run_thresholds), and the run ``status``.

    An evaluator the gate did not judge gets no bar: a model gate's threshold
    belongs to its one judged score, never to the rest. Verdicts compare the
    unrounded means the gate compared; the rounded mean is display-only.
    """
    means = {name: sum(values) / len(values)
             for name, values in score_totals.items() if values}
    threshold, gate = resolve_run_thresholds(meta, None, means)
    bars = recorded_gate(meta, means) or {}

    aggregates = {}
    for name, values in score_totals.items():
        if not values:
            continue
        bar = bars.get(name)
        aggregates[name] = {
            "mean": round(means[name], 3),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "count": len(values),
            "pass_rate": round(sum(1 for v in values if v >= 0.5) / len(values), 3),
            "bar": bar,
            "cleared": None if bar is None else means[name] >= bar,
        }

    if cert_value is not None:
        # Same source of truth as the dashboard/history status, so a run never
        # shows PASSED on one page and UNKNOWN on another.
        status = "PASSED" if cert_value == 1.0 else "FAILED"
    elif bars:
        # Older runs without a persisted gate score: re-apply the bars the run
        # recorded, to exactly the scores they judged.
        status = replay_gate(bars, means)
    else:
        # No persisted gate score and no recorded bar — nothing to judge
        # against, so don't invent one.
        status = "UNKNOWN"

    return {"aggregates": aggregates, "threshold": threshold,
            "gate_thresholds": gate, "status": status}


class PortalClient:
    """Fetches and aggregates deployment-gate data from Langfuse.

    Reads only the Langfuse v4 API: datasets from ``/v2/datasets``, gate runs
    from ``/experiments`` (run-level scores — ``certification_result`` and every
    ``avg_*`` — are the experiment's own scores), and per-item results from
    ``/experiment-items``. No trace fetches, no SDK dataset fetch.
    """

    def __init__(self):
        host, auth = langfuse_creds()
        self.host = host
        self._auth = auth

    # /v2/datasets is page-based ({"meta": {"page", "totalPages"}}); the v4
    # experiment endpoints are cursor-based (cert_common.paginate_cursor).
    PAGE_SIZE = PAGE_SIZE
    MAX_PAGES = MAX_PAGES

    def _api_get(self, path, params=None):
        return api_get(path, params, host=self.host, auth=self._auth)

    def _paginate(self, path):
        """Fetch all pages of a page-numbered list endpoint (/v2/datasets)."""
        out = []
        page = 1
        while page <= self.MAX_PAGES:
            resp = self._api_get(path, {"limit": self.PAGE_SIZE, "page": page})
            batch = resp.get("data", []) or []
            out.extend(batch)
            total_pages = (resp.get("meta") or {}).get("totalPages")
            if total_pages is None:
                if len(batch) < self.PAGE_SIZE:
                    break
            elif page >= total_pages:
                break
            page += 1
        return out

    def _paginate_cursor(self, path, params):
        return paginate_cursor(path, params, host=self.host, auth=self._auth)

    # ---- Helpers ----

    def _dataset_ids(self):
        """{dataset name: id} for every dataset in the project (cached)."""
        key = "dataset_ids"
        if key in _cache:
            return _cache[key]
        try:
            ids = {d.get("name"): d.get("id")
                   for d in self._paginate("/api/public/v2/datasets")}
        except Exception as exc:
            logger.warning("Failed to list datasets (%s)", exc)
            return {}
        _cache[key] = ids
        return ids

    def _dataset_id(self, dataset_name):
        ds_id = self._dataset_ids().get(dataset_name)
        if ds_id:
            return ds_id
        try:
            encoded = urllib.parse.quote(dataset_name, safe="")
            return self._api_get(f"/api/public/v2/datasets/{encoded}").get("id")
        except Exception as exc:
            logger.warning("Dataset %s not found (%s)", dataset_name, exc)
            return None

    def _get_experiments(self, dataset_name, run_name=None):
        """The dataset's experiments (gate runs), each normalized by _experiment_row."""
        key = f"experiments:{dataset_name}:{run_name or ''}"
        if key in _cache:
            return _cache[key]
        ds_id = self._dataset_id(dataset_name)
        if not ds_id:
            return []
        params = {"datasetId": ds_id, "fromStartTime": lookback_from_env(),
                  "fields": "core,metadata,scores"}
        if run_name:
            params["name"] = run_name
        try:
            raw = self._paginate_cursor("/api/public/experiments", params)
        except Exception as exc:
            logger.warning("Failed to fetch experiments for dataset %s (%s); "
                           "treating as no runs", dataset_name, exc)
            return []
        rows = [self._experiment_row(e) for e in raw
                if not (e.get("name") or "").startswith(HIDDEN_RUN_PREFIXES)]
        _cache[key] = rows
        return rows

    @staticmethod
    def _experiment_row(exp):
        """One experiment as {id, name, ts, meta, cert}.

        ``cert`` holds the run-level scores: ``cert_value`` / ``cert_comment``
        from ``certification_result`` (BOOLEAN or legacy NUMERIC, coerced to
        1.0/0.0), plus every ``avg_*`` value.
        """
        cert = {}
        for s in exp.get("scores") or []:
            name = s.get("name") or ""
            if name == "certification_result":
                cert["cert_value"] = score_value(s)
                cert["cert_comment"] = s.get("comment") or ""
            elif name.startswith("avg_"):
                cert[name] = score_value(s)
        return {
            "id": exp.get("id"),
            "name": exp.get("name", ""),
            "ts": exp.get("startTime") or exp.get("createdAt") or "",
            "meta": normalize_run_metadata(exp.get("metadata")),
            "cert": cert,
        }

    @staticmethod
    def _status(cert):
        cert_value = cert.get("cert_value")
        if cert_value is None:
            return "UNKNOWN"
        return "PASSED" if cert_value == 1.0 else "FAILED"

    @staticmethod
    def _pick_primary_score(cert):
        """Pick a run's primary score via the documented fallback chain.

        Returns {"name": <avg_* score name>, "value": <float>} for the first
        chain entry present on the run; falls back to the first other avg_*
        score (alphabetical); else {"name": None, "value": None}.
        """
        for name in PRIMARY_SCORE_CHAIN:
            if cert.get(name) is not None:
                return {"name": name, "value": cert[name]}
        for name in sorted(cert):
            if name.startswith("avg_") and cert[name] is not None:
                return {"name": name, "value": cert[name]}
        return {"name": None, "value": None}

    @staticmethod
    def _parse_model_from_run_name(name):
        """Best-effort dashboard row key for a run that recorded no metadata.

        Runs recorded by SDK 3.x come back from /experiments with ``{}``
        metadata, so the row key is recovered from the run name the runners
        generate: ``<use-case>-<model>-<dataset>-<ts>`` (agent gate, keyed
        ``usecase:<use-case>`` exactly as the runner records it) or
        ``<model>-<dataset>-<ts>`` (model gate).
        """
        for use_case in KNOWN_USE_CASES:
            if name.startswith(use_case + "-"):
                return f"usecase:{use_case}"
        parts = name.split("-")
        for i, p in enumerate(parts):
            if p in DATASET_TOKENS:
                return "-".join(parts[:i])
        return name

    def _model_of(self, row):
        return row["meta"].get("model") or self._parse_model_from_run_name(row["name"])

    # ---- Public methods ----

    def list_datasets(self):
        """Dataset names to display in the portal.

        Resolution order:
        1. ``PORTAL_DATASETS`` env var — comma-separated dataset names,
           returned exactly as given (trimmed, order preserved).
        2. Discovery — all Langfuse datasets whose name starts with
           ``PORTAL_DATASET_PREFIX`` (default ``certification/``), sorted
           alphabetically. Cached for 60s.
        3. ``FALLBACK_DATASETS`` if the Langfuse API call fails, so the portal
           degrades instead of erroring.
        """
        override = os.getenv("PORTAL_DATASETS", "")
        if override.strip():
            return [name.strip() for name in override.split(",") if name.strip()]

        key = "datasets"
        if key in _cache:
            return _cache[key]

        prefix = os.getenv("PORTAL_DATASET_PREFIX", DEFAULT_DATASET_PREFIX)
        try:
            data = self._paginate("/api/public/v2/datasets")
        except Exception as exc:
            logger.warning(
                "Failed to list datasets from Langfuse (%s); "
                "falling back to the known default datasets", exc,
            )
            return list(FALLBACK_DATASETS)

        _cache["dataset_ids"] = {d.get("name"): d.get("id") for d in data}
        names = sorted(
            d.get("name", "") for d in data
            if (d.get("name") or "").startswith(prefix)
        )
        _cache[key] = names
        return names

    def _summary_row(self, row):
        """Fields shared by dashboard and history rows."""
        cert = row["cert"]
        primary = self._pick_primary_score(cert)
        threshold, gate = resolve_run_thresholds(
            row["meta"], primary["name"], _run_score_names(cert))
        return {
            "model": self._model_of(row),
            "status": self._status(cert),
            "primary_score": primary,
            "threshold": threshold,
            "gate_thresholds": gate,
            "run_name": row["name"],
            "cert_comment": cert.get("cert_comment", ""),
        }

    def get_dashboard_data(self):
        """Get gate status for all model x dataset combinations."""
        key = "dashboard"
        if key in _cache:
            return _cache[key]

        rows = []
        for ds_name in self.list_datasets():
            model_latest = {}
            for row in self._get_experiments(ds_name):
                model = self._model_of(row)
                if not model:
                    continue
                if model not in model_latest or row["ts"] > model_latest[model]["ts"]:
                    model_latest[model] = row

            for row in model_latest.values():
                rows.append({
                    **self._summary_row(row),
                    "dataset": ds_name,
                    "dataset_short": ds_name.split("/")[-1],
                    "timestamp": row["ts"][:10],
                })

        rows.sort(key=lambda x: (x["dataset"], x["model"]))
        _cache[key] = rows
        return rows

    def get_run_breakdown(self, dataset_name, run_name):
        """Get aggregated evaluator scores for a specific run."""
        return self._collect_run_data(dataset_name, run_name)

    def get_history(self, dataset_name):
        """Get all runs for a dataset with gate results."""
        key = f"history:{dataset_name}"
        if key in _cache:
            return _cache[key]

        runs = [{**self._summary_row(row), "timestamp": row["ts"][:19]}
                for row in self._get_experiments(dataset_name)]
        runs.sort(key=lambda x: x["timestamp"], reverse=True)
        _cache[key] = runs
        return runs

    def get_run_detail(self, dataset_name, run_name):
        """Get per-item scores for a specific run."""
        return self._collect_run_data(dataset_name, run_name)

    def _collect_run_data(self, dataset_name, run_name):
        """One run's items and scores, from /experiments + /experiment-items."""
        key = f"run:{dataset_name}:{run_name}"
        if key in _cache:
            return _cache[key]

        matches = [r for r in self._get_experiments(dataset_name, run_name)
                   if r["name"] == run_name]
        if not matches:
            return {"error": f"Run '{run_name}' not found",
                    "dataset": dataset_name,
                    "dataset_short": dataset_name.split("/")[-1],
                    "run_name": run_name,
                    "threshold": None, "gate_thresholds": None,
                    "total_items": 0, "items": [],
                    "aggregates": {}, "model": "", "status": "UNKNOWN",
                    "score_names": [], "langfuse_url": self.host}
        run = max(matches, key=lambda r: r["ts"])
        meta = run["meta"]

        try:
            run_items = self._paginate_cursor("/api/public/experiment-items", {
                "experimentId": run["id"],
                "fromStartTime": lookback_from_env(),
                "fields": "core,io,scores,itemMetadata",
            })
        except Exception as exc:
            logger.warning("Failed to fetch items for run %s (%s)", run_name, exc)
            run_items = []

        items_data = []
        score_totals = {}
        for ri in run_items:
            item_scores = {}
            for s in ri.get("scores") or []:
                sname = s.get("name")
                # Run-level scores live on the experiment, not the item; skip
                # any copy a pre-v5 runner also wrote onto an item trace.
                if not sname or sname in RUN_LEVEL_SCORES or sname.startswith("avg_"):
                    continue
                sval = score_value(s)
                item_scores[sname] = {"value": sval, "comment": s.get("comment") or ""}
                score_totals.setdefault(sname, [])
                if sval is not None:
                    score_totals[sname].append(sval)

            inp = parse_json_field(ri.get("input"))
            expected = parse_json_field(ri.get("expectedOutput"))
            items_data.append({
                "trace_id": ri.get("traceId", ""),
                "input": inp if inp is not None else {},
                "expected_output": expected if expected is not None else {},
                "question": (str(inp.get("question", inp.get("text", "")))[:120]
                             if isinstance(inp, dict) else str(inp or "")[:120]),
                "expected_short": self._format_expected(expected),
                "scores": item_scores,
            })

        summary = summarize_run(score_totals, meta, run["cert"].get("cert_value"))

        all_score_names = sorted(set(
            name for item in items_data for name in item["scores"]
        ))

        result = {
            "dataset": dataset_name,
            "dataset_short": dataset_name.split("/")[-1],
            "run_name": run_name,
            "model": self._model_of(run),
            **summary,
            "total_items": len(items_data),
            "items": items_data,
            "score_names": all_score_names,
            "langfuse_url": self.host,
        }
        _cache[key] = result
        return result

    @staticmethod
    def _format_expected(expected):
        if not expected or not isinstance(expected, dict):
            return ""
        answer = expected.get("answer", expected.get("sentiment", ""))
        return str(answer)[:80] if answer else ""
