"""
Langfuse data layer for the Certification Portal.

Fetches experiment data from Langfuse, aggregates scores, and caches results.
"""

import base64
import json
import os
import urllib.parse
import urllib.request

from cachetools import TTLCache
from langfuse import Langfuse


_cache = TTLCache(maxsize=64, ttl=60)

# Datasets to display in the portal
DATASETS = [
    "certification/financebench-sample",
    "certification/fpb-sample",
    "certification/financebench-v1",
    "certification/fpb-v1",
]

# Score names that are run-level aggregates (not per-item)
RUN_LEVEL_SCORES = {
    "certification_result", "avg_numerical_accuracy",
    "avg_sentiment_accuracy", "avg_groundedness",
}


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
        except Exception:
            return []

    def _get_scores_by_name(self, name):
        """Fetch all scores with a given name (paginated)."""
        try:
            return self._paginate(f"/api/public/scores?name={urllib.parse.quote(name)}")
        except Exception:
            return []

    def _get_trace(self, trace_id):
        """Fetch a trace by ID."""
        try:
            return self._api_get(f"/api/public/traces/{trace_id}")
        except Exception:
            return None

    def _build_cert_index(self):
        """Build an index of run_name -> {cert_value, cert_comment, avg_score}.

        Fetches certification_result and avg_* scores, resolves their trace
        metadata to find the experiment_run_name, and returns a lookup dict.
        """
        key = "cert_index"
        if key in _cache:
            return _cache[key]

        index = {}  # run_name -> {cert_value, cert_comment, primary_score}

        # Get certification_result scores
        cert_scores = self._get_scores_by_name("certification_result")
        for s in cert_scores:
            trace = self._get_trace(s["traceId"])
            if not trace:
                continue
            meta = trace.get("metadata") or {}
            run_name = meta.get("experiment_run_name", "")
            if run_name:
                if run_name not in index:
                    index[run_name] = {}
                index[run_name]["cert_value"] = s["value"]
                index[run_name]["cert_comment"] = s.get("comment", "")

        # Get avg scores
        for avg_name in ["avg_numerical_accuracy", "avg_sentiment_accuracy", "avg_groundedness"]:
            avg_scores = self._get_scores_by_name(avg_name)
            for s in avg_scores:
                trace = self._get_trace(s["traceId"])
                if not trace:
                    continue
                meta = trace.get("metadata") or {}
                run_name = meta.get("experiment_run_name", "")
                if run_name:
                    if run_name not in index:
                        index[run_name] = {}
                    index[run_name][avg_name] = s["value"]

        _cache[key] = index
        return index

    @staticmethod
    def _primary_score_name(dataset_name):
        is_sentiment = "fpb" in dataset_name.lower()
        return "sentiment_accuracy" if is_sentiment else "numerical_accuracy"

    @staticmethod
    def _parse_model_from_run_name(name):
        parts = name.split("-")
        for i, p in enumerate(parts):
            if p in ("financebench", "fpb"):
                return "-".join(parts[:i])
        return name

    # ---- Public methods ----

    def get_dashboard_data(self):
        """Get certification status for all model x dataset combinations."""
        key = "dashboard"
        if key in _cache:
            return _cache[key]

        cert_index = self._build_cert_index()
        rows = []

        for ds_name in DATASETS:
            runs = self._get_runs_for_dataset(ds_name)
            if not runs:
                continue

            primary_name = self._primary_score_name(ds_name)
            avg_name = f"avg_{primary_name}"

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
                primary_score = cert.get(avg_name)

                if cert_value is not None:
                    status = "PASSED" if cert_value == 1.0 else "FAILED"
                else:
                    status = "UNKNOWN"

                rows.append({
                    "model": model,
                    "dataset": ds_name,
                    "dataset_short": ds_name.split("/")[-1],
                    "status": status,
                    "primary_score": primary_score,
                    "primary_name": primary_name,
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

        primary_name = self._primary_score_name(dataset_name)
        avg_name = f"avg_{primary_name}"

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
                "primary_score": cert.get(avg_name),
                "primary_name": primary_name,
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
            return {"error": f"Run '{run_name}' not found", "items": [],
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

        for ri in run_items:
            trace_id = ri.get("traceId", "")
            item_scores = {}

            # Get all scores for this trace (paginated — a trace can have many)
            try:
                trace_scores = self._paginate(
                    f"/api/public/scores?traceId={trace_id}"
                )
                for s in trace_scores:
                    sname = s["name"]
                    if sname in RUN_LEVEL_SCORES:
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
            except Exception:
                pass

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

        primary_name = self._primary_score_name(dataset_name)
        primary_agg = aggregates.get(primary_name, {})
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
