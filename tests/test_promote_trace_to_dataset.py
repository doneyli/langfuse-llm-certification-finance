"""Offline unit tests for the trace -> dataset feedback edge (loop edge A).

Exercises the pure payload/resolution logic in promote_trace_to_dataset.py and the
no-network guard in cert_common.queue_trace_ids. No network, no SDK calls.
"""
import argparse

import promote_trace_to_dataset as promote


class TestItemId:
    def test_deterministic_and_prefixed(self):
        # Deterministic id => re-running upserts instead of duplicating.
        assert promote.item_id_for("abc123") == "prod-abc123"
        assert promote.item_id_for("abc123") == promote.item_id_for("abc123")


class TestBuildDatasetItem:
    def _trace(self):
        return {"id": "t1", "input": {"question": "What was FY19 revenue?"},
                "output": {"answer": "wrong"}}

    def test_without_expected_flags_for_review(self):
        item = promote.build_dataset_item(self._trace())
        assert item["id"] == "prod-t1"
        assert item["source_trace_id"] == "t1"
        assert item["input"] == {"question": "What was FY19 revenue?"}
        # The suspect trace output is NOT copied as the expected answer.
        assert item["expected_output"] == {"answer": ""}
        assert item["metadata"]["needs_expected_review"] is True
        assert item["metadata"]["promoted_from"] == "production"

    def test_with_expected_answer(self):
        item = promote.build_dataset_item(self._trace(), expected="$52.6B")
        assert item["expected_output"] == {"answer": "$52.6B"}
        assert item["metadata"]["needs_expected_review"] is False

    def test_note_stored_in_metadata(self):
        item = promote.build_dataset_item(self._trace(), note="flagged 2026-07-07")
        assert item["metadata"]["note"] == "flagged 2026-07-07"

    def test_does_not_copy_trace_output(self):
        # Guard the core safety property: the flagged output never becomes expected.
        item = promote.build_dataset_item(self._trace(), expected=None)
        assert item["expected_output"]["answer"] != "wrong"


class TestResolveTraceIds:
    def _args(self, trace_id=None, from_queue=False):
        return argparse.Namespace(trace_id=trace_id or [], from_queue=from_queue)

    def test_explicit_ids_only(self):
        ids = promote.resolve_trace_ids(self._args(trace_id=["a", "b"]),
                                        from_queue_fn=lambda: ["should-not-be-used"])
        assert ids == ["a", "b"]

    def test_merges_queue_when_requested(self):
        ids = promote.resolve_trace_ids(self._args(trace_id=["a"], from_queue=True),
                                        from_queue_fn=lambda: ["b", "c"])
        assert ids == ["a", "b", "c"]

    def test_dedupes_across_sources_and_drops_falsy(self):
        ids = promote.resolve_trace_ids(self._args(trace_id=["a", "", "b"], from_queue=True),
                                        from_queue_fn=lambda: ["b", "d", None])
        assert ids == ["a", "b", "d"]


class TestQueueTraceIdsGuard:
    def test_empty_input_makes_no_request(self):
        # Returns 0 before touching the network for empty / all-falsy input.
        from cert_common import queue_trace_ids
        assert queue_trace_ids([]) == 0
        assert queue_trace_ids(["", None]) == 0
