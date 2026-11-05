"""Offline tests for the Langfuse v4 read path (no network).

Langfuse Cloud removed the legacy read API on 2026-11-16. The portal, the
evidence pack, the production monitor and the promoter now read
/experiments, /experiment-items, /v2/observations and /v3/scores. These tests
pin the shapes those endpoints return (verified live with
scripts/spike_span_nesting.py) and how the repo decodes them.
"""
import pytest

import cert_common
from cert_common import (normalize_run_metadata, parse_json_field,
                         recorded_gate, score_value)


# What GET /experiments?fields=metadata returns for a run recorded by SDK v5
# with metadata={"model": ..., "gate_thresholds": {...}}: flattened, stringified.
V5_AGENT_META = {
    "model": "usecase:advisory-draft",
    "use_case": "advisory-draft",
    "gate_thresholds.groundedness": "0.8",
    "gate_thresholds.regulatory_compliance": "1",
    "gate_thresholds.completeness": "0.7",
}
V5_MODEL_META = {"model": "claude-sonnet-4-6", "threshold": "0.85",
                 "gate": "numerical_accuracy"}


class TestRunMetadata:
    def test_flattened_gate_is_nested_again(self):
        meta = normalize_run_metadata(V5_AGENT_META)
        assert meta["gate_thresholds"] == {"groundedness": "0.8",
                                           "regulatory_compliance": "1",
                                           "completeness": "0.7"}
        assert meta["model"] == "usecase:advisory-draft"

    def test_agent_gate_round_trips_as_float_bars(self):
        assert recorded_gate(V5_AGENT_META) == {
            "groundedness": 0.8, "regulatory_compliance": 1.0, "completeness": 0.7}

    def test_stringified_model_threshold_is_a_bar(self):
        assert recorded_gate(V5_MODEL_META) == {"numerical_accuracy": 0.85}

    def test_json_string_gate_is_decoded(self):
        assert recorded_gate({"gate_thresholds": '{"groundedness": 0.8}'}) == {
            "groundedness": 0.8}

    def test_legacy_runs_have_empty_metadata_and_no_bar(self):
        assert normalize_run_metadata({}) == {}
        assert normalize_run_metadata(None) == {}
        assert recorded_gate({}) is None

    def test_native_dicts_still_work(self):
        meta = {"gate_thresholds": {"groundedness": 0.8}, "model": "m"}
        assert normalize_run_metadata(meta) == meta

    def test_non_numeric_string_is_not_a_bar(self):
        assert recorded_gate({"threshold": "high", "gate": "x"}) is None


class TestScoreValue:
    @pytest.mark.parametrize("raw,expected", [
        (True, 1.0), (False, 0.0), (1, 1.0), (0.85, 0.85),
        ("true", 1.0), ("false", 0.0), ("0.5", 0.5), ("Fully Grounded", None),
        (None, None),
    ])
    def test_coercion(self, raw, expected):
        assert score_value({"value": raw}) == expected


class TestParseJsonField:
    def test_object_and_string(self):
        assert parse_json_field('{"answer": "x"}') == {"answer": "x"}
        assert parse_json_field("positive") == "positive"
        assert parse_json_field('{"broken') == '{"broken'
        assert parse_json_field({"already": "dict"}) == {"already": "dict"}


class TestCursorPagination:
    def test_follows_meta_cursor_until_absent(self, monkeypatch):
        pages = {None: {"data": [1, 2], "meta": {"cursor": "c1"}},
                 "c1": {"data": [3], "meta": {"cursor": "c2"}},
                 "c2": {"data": [], "meta": {}}}
        seen = []

        def fake_get(path, params=None, **kw):
            seen.append(dict(params))
            return pages[params.get("cursor")]

        monkeypatch.setattr(cert_common, "api_get", fake_get)
        assert cert_common.paginate_cursor("/x", {"a": 1}) == [1, 2, 3]
        assert all(p["limit"] == 100 and p["a"] == 1 for p in seen)

    def test_iso_utc_is_z_suffixed(self):
        assert cert_common.iso_utc().endswith("Z")


# --------------- Portal on /experiments + /experiment-items ---------------

def _experiment(name, start, meta, scores):
    return {"id": f"id-{name}", "name": name, "startTime": start,
            "metadata": meta,
            "scores": [{"name": n, "value": v, "comment": f"{n} c"}
                       for n, v in scores.items()]}


EXPERIMENTS = [
    _experiment("advisory-draft-claude-sonnet-4-6-advisory-adversarial-20260924-1",
                "2026-09-24T18:58:17Z", {},
                {"certification_result": 0, "avg_groundedness": 0.976}),
    _experiment("advisory-draft-claude-sonnet-4-6-advisory-adversarial-20261104-1",
                "2026-11-04T10:00:00Z", V5_AGENT_META,
                {"certification_result": True, "avg_groundedness": 0.9}),
    _experiment("spike-v5-20261104", "2026-11-04T11:00:00Z", {"model": "spike"},
                {"spike_run_score": 1}),
]

ITEMS = [
    {"traceId": "t1", "input": '{"question": "Write an update"}',
     "expectedOutput": '{"answer": "Revenue grew"}',
     "scores": [{"name": "groundedness", "value": 0.9},
                {"name": "regulatory_compliance", "value": True},
                {"name": "completeness", "value": 1},
                {"name": "certification_result", "value": 1}]},
    {"traceId": "t2", "input": '{"question": "Another"}', "expectedOutput": "null",
     "scores": [{"name": "groundedness", "value": 0.9},
                {"name": "regulatory_compliance", "value": False},
                {"name": "completeness", "value": 0.4}]},
]


@pytest.fixture
def portal(monkeypatch):
    from portal import langfuse_client as lc
    lc._cache.clear()
    client = lc.PortalClient.__new__(lc.PortalClient)
    client.host, client._auth = "https://lf.test", "x"
    monkeypatch.setattr(client, "_dataset_id", lambda name: "ds1")
    monkeypatch.setattr(client, "list_datasets",
                        lambda: ["certification/advisory-adversarial"])

    def fake_cursor(path, params):
        if path.endswith("/experiments"):
            rows = EXPERIMENTS
            if params.get("name"):
                rows = [e for e in rows if e["name"] == params["name"]]
            return rows
        if path.endswith("/experiment-items"):
            return ITEMS
        raise AssertionError(path)

    monkeypatch.setattr(client, "_paginate_cursor", fake_cursor)
    yield client
    lc._cache.clear()


class TestPortalV4:
    def test_dashboard_takes_latest_run_per_row_and_hides_spikes(self, portal):
        rows = portal.get_dashboard_data()
        assert [r["model"] for r in rows] == ["usecase:advisory-draft"]
        row = rows[0]
        assert row["status"] == "PASSED"  # BOOLEAN true certification_result
        assert row["primary_score"] == {"name": "avg_groundedness", "value": 0.9}
        assert row["threshold"] == 0.8
        assert row["gate_thresholds"]["regulatory_compliance"] == 1.0

    def test_history_renders_legacy_runs_without_bars(self, portal):
        runs = portal.get_history("certification/advisory-adversarial")
        assert [r["status"] for r in runs] == ["PASSED", "FAILED"]
        legacy = runs[1]
        assert legacy["model"] == "usecase:advisory-draft"  # from the run name
        assert legacy["threshold"] is None and legacy["gate_thresholds"] is None

    def test_run_detail_reads_items_and_run_level_score(self, portal):
        name = EXPERIMENTS[1]["name"]
        detail = portal.get_run_detail("certification/advisory-adversarial", name)
        assert detail["total_items"] == 2
        assert detail["score_names"] == ["completeness", "groundedness",
                                         "regulatory_compliance"]
        assert detail["items"][0]["question"] == "Write an update"
        assert detail["items"][0]["expected_short"] == "Revenue grew"
        agg = detail["aggregates"]["regulatory_compliance"]
        assert agg["mean"] == 0.5 and agg["bar"] == 1.0 and agg["cleared"] is False
        # Status is the experiment's own gate score, not a replay of the items.
        assert detail["status"] == "PASSED"

    def test_unknown_run(self, portal):
        detail = portal.get_run_detail("certification/advisory-adversarial", "nope")
        assert detail["status"] == "UNKNOWN" and "error" in detail


# --------------- Monitor on /v2/observations ---------------

class TestMonitorV4:
    def test_filter_shapes(self):
        from monitor_production import observation_filter
        assert observation_filter() == []
        f = observation_filter(tags=["production"], trace_name="app")
        assert {"type": "string", "column": "traceName", "operator": "=",
                "value": "app"} in f
        assert {"type": "arrayOptions", "column": "tags", "operator": "any of",
                "value": ["production"]} in f

    def test_agent_json_output_is_scored_on_its_answer(self):
        from monitor_production import evaluate_trace
        evs = evaluate_trace({"output": '{"answer": "These are guaranteed returns."}'})
        compliance = next(e for e in evs if e.name == "regulatory_compliance")
        assert compliance.value == 0.0

    def test_empty_output_is_skipped(self):
        from monitor_production import evaluate_trace
        assert evaluate_trace({"output": None}) == []


class TestDatasetVersion:
    def test_newest_item_change_is_the_version(self):
        from datetime import datetime, timezone
        from types import SimpleNamespace
        from cert_common import dataset_version
        items = [SimpleNamespace(updated_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                                 created_at=None),
                 SimpleNamespace(updated_at=None,
                                 created_at=datetime(2026, 9, 2, 3, 4, 5))]
        assert dataset_version(items) == "2026-09-02T03:04:05.000Z"
        assert dataset_version([]) is None

    def test_evidence_pack_names_the_version(self):
        from export_results import format_markdown
        report = format_markdown({
            "dataset": "certification/fpb-sample", "run_name": "r",
            "run_metadata": {"dataset_version": "2026-09-02T03:04:05.000Z",
                             "dataset_item_count": "10"},
            "exported_at": "2026-11-04T00:00:00", "total_items": 0,
            "aggregates": {}, "items": []})
        assert "| **Dataset version** | 2026-09-02T03:04:05.000Z (10 items) |" in report
