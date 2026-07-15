"""
Langfuse data layer for the Certification Portal.

Fetches experiment data from Langfuse, aggregates scores, and caches results.
"""

import base64
import json
import logging
import os
import urllib.parse
import urllib.request

from cachetools import TTLCache
from langfuse import Langfuse


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
# Any `avg_*` score is also run-level (see cert_common.persist_run_evaluations).
RUN_LEVEL_SCORES = {"certification_result"}

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


class PortalClient:
    """Fetches and aggregates certification data from Langfuse."""

    def __init__(self):
        host = os.getenv("LANGFUSE_BASE_URL",
                         os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
        pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
        sk = os.getenv("LANGFUSE_SECRET_KEY", "")

        self.host = host
        self._auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()
        self._sdk = Langfuse(public_key=pk, secret_key=sk, host=host)

    # Langfuse REST API enforces max limit=100 per page and returns
    # {"data": [...], "meta": {"page", "limit", "totalItems", "totalPages"}}.
    # Keep a hard ceiling on pages as a circuit breaker against misconfigured
    # queries fanning into thousands of requests.
    PAGE_SIZE = 100
    MAX_PAGES = 100  # => 10k items max per paginated call

    def _api_get(self, path):
        req = urllib.request.Request(
            f"{self.host}{path}",
            headers={"Authorization": f"Basic {self._auth}"},
        )
        resp = urllib.request.urlopen(req)
        return json.loads(resp.read())

    def _paginate(self, path):
        """Fetch all pages of a Langfuse list endpoint.

        `path` may include query params; page/limit are appended by this helper.
        Returns the concatenated `data` array.
        """
        sep = "&" if "?" in path else "?"
        out = []
        page = 1
        while page <= self.MAX_PAGES:
            resp = self._api_get(f"{path}{sep}limit={self.PAGE_SIZE}&page={page}")
            batch = resp.get("data", []) or []
            out.extend(batch)
            meta = resp.get("meta") or {}
            total_pages = meta.get("totalPages")
            if total_pages is None:
                # No meta -> fall back to "stop when batch is short"
                if len(batch) < self.PAGE_SIZE:
                    break
            elif page >= total_pages:
                break
            page += 1
        return out

    # ---- Helpers ----

    def _get_runs_for_dataset(self, dataset_name):
        """Fetch all runs for a dataset via paginated REST API."""
        encoded = urllib.parse.quote(dataset_name, safe="")
        try:
            return self._paginate(f"/api/public/datasets/{encoded}/runs")
        except Exception as exc:
            logger.warning("Failed to fetch runs for dataset %s (%s); "
                           "treating as no runs", dataset_name, exc)
            return []

    def _get_scores_by_name(self, name):
        """Fetch all scores with a given name (paginated)."""
        try:
            return self._paginate(f"/api/public/scores?name={urllib.parse.quote(name)}")
        except Exception as exc:
            logger.warning("Failed to fetch scores named %s (%s); "
                           "treating as no scores", name, exc)
            return []

    def _get_trace(self, trace_id):
        """Fetch a trace by ID."""
        try:
            return self._api_get(f"/api/public/traces/{trace_id}")
        except Exception as exc:
            logger.warning("Failed to fetch trace %s (%s)", trace_id, exc)
            return None

    def _build_cert_index(self):
        """Build an index of run_name -> {cert_value, cert_comment, avg_* scores}.

        Run-level scores (certification_result + every avg_*) are all attached
        to the same summary trace (see cert_common.persist_run_evaluations), so
        after resolving an anchor score's trace we harvest every run-level
        score riding on it — including avg_* names we don't know in advance
        (e.g. avg_regulatory_compliance from the advisory use case).
        """
        key = "cert_index"
        if key in _cache:
            return _cache[key]

        index = {}       # run_name -> {cert_value, cert_comment, avg_*: value}
        trace_memo = {}  # trace_id -> trace (avoid re-fetching shared traces)
        harvested = set()

        anchor_names = ["certification_result"] + PRIMARY_SCORE_CHAIN
        for anchor in anchor_names:
            for s in self._get_scores_by_name(anchor):
                trace_id = s.get("traceId")
                if not trace_id:
                    continue
                if trace_id not in trace_memo:
                    trace_memo[trace_id] = self._get_trace(trace_id)
                trace = trace_memo[trace_id]
                if not trace:
                    continue
                meta = trace.get("metadata") or {}
                run_name = meta.get("experiment_run_name", "")
                if not run_name:
                    continue
                entry = index.setdefault(run_name, {})

                # The anchor score itself (authoritative value from /scores).
                if anchor == "certification_result":
                    entry["cert_value"] = s.get("value")
                    entry["cert_comment"] = s.get("comment", "") or ""
                else:
                    entry[anchor] = s.get("value")

                # Harvest every run-level score on the same trace once —
                # this picks up avg_* names outside the anchor list.
                if trace_id in harvested:
                    continue
                harvested.add(trace_id)
                for ts in trace.get("scores", []) or []:
                    name = ts.get("name") or ""
                    if name == "certification_result":
                        entry.setdefault("cert_value", ts.get("value"))
                        entry.setdefault("cert_comment", ts.get("comment", "") or "")
                    elif name.startswith("avg_"):
                        entry.setdefault(name, ts.get("value"))

        _cache[key] = index
        return index

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
        parts = name.split("-")
        for i, p in enumerate(parts):
            if p in ("financebench", "fpb"):
                return "-".join(parts[:i])
        return name

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

        names = sorted(
            d.get("name", "") for d in data
            if (d.get("name") or "").startswith(prefix)
        )
        _cache[key] = names
        return names

    def get_dashboard_data(self):
        """Get certification status for all model x dataset combinations."""
        key = "dashboard"
        if key in _cache:
            return _cache[key]

        cert_index = self._build_cert_index()
        rows = []

        for ds_name in self.list_datasets():
            runs = self._get_runs_for_dataset(ds_name)
            if not runs:
                continue

            # Group by model, pick latest
            model_latest = {}
            for r in runs:
                meta = r.get("metadata") or {}
                model = meta.get("model", self._parse_model_from_run_name(r.get("name", "")))
                if not model:
                    continue
                ts = r.get("createdAt", "")
                if model not in model_latest or ts > model_latest[model]["ts"]:
                    model_latest[model] = {"ts": ts, "run": r}

            for model, info in model_latest.items():
                r = info["run"]
                meta = r.get("metadata") or {}
                run_name = r.get("name", "")
                cert = cert_index.get(run_name, {})

                cert_value = cert.get("cert_value")

                if cert_value is not None:
                    status = "PASSED" if cert_value == 1.0 else "FAILED"
                else:
                    status = "UNKNOWN"

                rows.append({
                    "model": model,
                    "dataset": ds_name,
                    "dataset_short": ds_name.split("/")[-1],
                    "status": status,
                    "primary_score": self._pick_primary_score(cert),
                    "threshold": meta.get("threshold", 0.85),
                    "run_name": run_name,
                    "timestamp": info["ts"][:10] if info["ts"] else "",
                    "cert_comment": cert.get("cert_comment", ""),
                })

        rows.sort(key=lambda x: (x["dataset"], x["model"]))
        _cache[key] = rows
        return rows

    def get_run_breakdown(self, dataset_name, run_name):
        """Get aggregated evaluator scores for a specific run."""
        return self._collect_run_data(dataset_name, run_name)

    def get_history(self, dataset_name):
        """Get all runs for a dataset with certification results."""
        key = f"history:{dataset_name}"
        if key in _cache:
            return _cache[key]

        runs_raw = self._get_runs_for_dataset(dataset_name)
        cert_index = self._build_cert_index()

        runs = []
        for r in runs_raw:
            meta = r.get("metadata") or {}
            run_name = r.get("name", "")
            cert = cert_index.get(run_name, {})

            cert_value = cert.get("cert_value")
            status = "UNKNOWN"
            if cert_value is not None:
                status = "PASSED" if cert_value == 1.0 else "FAILED"

            runs.append({
                "run_name": run_name,
                "model": meta.get("model", self._parse_model_from_run_name(run_name)),
                "status": status,
                "primary_score": self._pick_primary_score(cert),
                "threshold": meta.get("threshold", 0.85),
                "timestamp": r.get("createdAt", "")[:19],
                "cert_comment": cert.get("cert_comment", ""),
            })

        runs.sort(key=lambda x: x["timestamp"], reverse=True)
        _cache[key] = runs
        return runs

    def get_run_detail(self, dataset_name, run_name):
        """Get per-item scores for a specific run."""
        return self._collect_run_data(dataset_name, run_name)

    def _collect_run_data(self, dataset_name, run_name):
        """Collect run data via REST API."""
        key = f"run:{dataset_name}:{run_name}"
        if key in _cache:
            return _cache[key]

        # Get dataset ID and run metadata
        dataset = self._sdk.get_dataset(dataset_name)
        runs_raw = self._get_runs_for_dataset(dataset_name)
        target_run = None
        for r in runs_raw:
            if r.get("name") == run_name:
                target_run = r
                break

        if not target_run:
            return {"error": f"Run '{run_name}' not found",
                    "dataset": dataset_name,
                    "dataset_short": dataset_name.split("/")[-1],
                    "run_name": run_name, "threshold": 0.85,
                    "total_items": 0, "items": [],
                    "aggregates": {}, "model": "", "status": "UNKNOWN",
                    "score_names": [], "langfuse_url": self.host}

        meta = target_run.get("metadata") or {}

        # Get all run items via paginated REST
        ds_id = dataset.id
        try:
            encoded_run = urllib.parse.quote(run_name)
            run_items = self._paginate(
                f"/api/public/dataset-run-items?datasetId={ds_id}&runName={encoded_run}"
            )
        except Exception:
            run_items = []

        # Build dataset item lookup
        ds_items = {item.id: item for item in dataset.items}

        items_data = []
        score_totals = {}
        # Run-level scores (certification_result + avg_*) ride on the first
        # experiment trace (see cert_common.persist_run_evaluations).
        run_scores = {}

        for ri in run_items:
            trace_id = ri.get("traceId", "")
            item_scores = {}

            # Read scores embedded in the trace itself.
            # (NOTE: /api/public/scores?traceId=... silently ignores the filter
            # and returns scores from every trace, so we cannot use it here.)
            trace = self._get_trace(trace_id) if trace_id else None
            for s in (trace or {}).get("scores", []) or []:
                sname = s.get("name")
                if not sname:
                    continue
                if sname in RUN_LEVEL_SCORES or sname.startswith("avg_"):
                    if s.get("value") is not None:
                        run_scores.setdefault(sname, s.get("value"))
                    continue
                sval = s.get("value")
                item_scores[sname] = {
                    "value": sval,
                    "comment": s.get("comment", ""),
                }
                if sname not in score_totals:
                    score_totals[sname] = []
                if sval is not None:
                    score_totals[sname].append(sval)

            # Get dataset item input/expected
            ds_item_id = ri.get("datasetItemId", "")
            ds_item = ds_items.get(ds_item_id)
            inp = ds_item.input if ds_item else {}
            expected = ds_item.expected_output if ds_item else {}

            items_data.append({
                "trace_id": trace_id,
                "input": inp,
                "expected_output": expected,
                "question": (inp.get("question", inp.get("text", ""))[:120]
                             if isinstance(inp, dict) else str(inp)[:120]),
                "expected_short": self._format_expected(expected),
                "scores": item_scores,
            })

        aggregates = {}
        for name, values in score_totals.items():
            if values:
                aggregates[name] = {
                    "mean": round(sum(values) / len(values), 3),
                    "min": round(min(values), 3),
                    "max": round(max(values), 3),
                    "count": len(values),
                    "pass_rate": round(
                        sum(1 for v in values if v >= 0.5) / len(values), 3
                    ),
                }

        cert_value = run_scores.get("certification_result")
        if cert_value is not None:
            # Same source of truth as the dashboard/history status, so a run
            # never shows PASSED on one page and UNKNOWN on another.
            status = "PASSED" if cert_value == 1.0 else "FAILED"
        else:
            # Older runs without a persisted gate score: judge the first
            # item-level score from the primary chain against the threshold.
            item_chain = [n.removeprefix("avg_") for n in PRIMARY_SCORE_CHAIN]
            primary_name = next(
                (n for n in item_chain if n in aggregates),
                min(set(aggregates) - set(item_chain), default=None),
            )
            primary_agg = aggregates.get(primary_name) if primary_name else None
            if primary_agg:
                threshold = meta.get("threshold", 0.85)
                status = "PASSED" if primary_agg["mean"] >= threshold else "FAILED"
            else:
                status = "UNKNOWN"

        all_score_names = sorted(set(
            name for item in items_data for name in item["scores"]
        ))

        result = {
            "dataset": dataset_name,
            "dataset_short": dataset_name.split("/")[-1],
            "run_name": run_name,
            "model": meta.get("model", self._parse_model_from_run_name(run_name)),
            "threshold": meta.get("threshold", 0.85),
            "status": status,
            "total_items": len(items_data),
            "aggregates": aggregates,
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
