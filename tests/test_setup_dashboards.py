"""Offline shape checks for setup_dashboards.py (unstable dashboards API)."""
import setup_dashboards as sd

VIEWS = {"observations", "scores-numeric", "scores-boolean", "scores-categorical"}
CHARTS = {"LINE_TIME_SERIES", "AREA_TIME_SERIES", "BAR_TIME_SERIES", "HORIZONTAL_BAR",
          "VERTICAL_BAR", "PIE", "NUMBER", "HISTOGRAM", "PIVOT_TABLE"}
AGGS = {"sum", "avg", "count", "max", "min", "p50", "p75", "p90", "p95", "p99",
        "histogram", "uniq"}


def test_widgets_use_allowed_enums_and_required_fields():
    assert len(sd.WIDGETS) == 4
    for w in sd.WIDGETS:
        assert {"name", "view", "dimensions", "metrics", "filters", "chartType"} <= set(w)
        assert w["view"] in VIEWS and w["chartType"] in CHARTS
        assert all(m["agg"] in AGGS for m in w["metrics"])


def test_compliance_widget_reads_the_boolean_score():
    w = next(w for w in sd.WIDGETS if "compliance" in w["name"])
    assert w["view"] == "scores-boolean"
    assert w["filters"] == [{"column": "name", "operator": "=", "type": "string",
                             "value": "regulatory_compliance"}]


def test_placements_tile_a_two_by_two_grid():
    ps = sd.placements(["a", "b", "c", "d"])
    assert [(p["x"], p["y"]) for p in ps] == [(0, 0), (6, 0), (0, 6), (6, 6)]
    assert all(p["type"] == "widget" and p["width"] == 6 for p in ps)
