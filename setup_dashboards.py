#!/usr/bin/env python3
"""
Create the deployment-gate dashboard in Langfuse (dashboards as code)

Builds the four widgets the README recommends and places them on one
dashboard, "Deployment gate — quality & cost":

  1. Avg scores over time   scores-numeric  avg(value) by name, line time series
  2. Compliance true-rate   scores-boolean  avg(value) for regulatory_compliance
                                            (BOOLEAN: share of clean outputs), line
  3. Score distribution     scores-numeric  avg(value) by name, vertical bar
  4. Cost by model          observations    sum(totalCost) by providedModelName, bar

Uses Langfuse's **unstable** dashboards API (/api/public/unstable/dashboards,
/dashboard-widgets, /placements): shapes may change between Langfuse
releases. Idempotent by name — an existing widget or dashboard with the same
name is reused, and a widget already placed on the dashboard is not placed
again.

Usage:
    python setup_dashboards.py --dry-run     # print the payloads, create nothing
    python setup_dashboards.py               # create missing widgets + dashboard
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

from cert_common import api_get, langfuse_creds


DASHBOARD_NAME = "Deployment gate — quality & cost"
DASHBOARD_DESCRIPTION = ("Score trends, compliance true-rate, score distribution and "
                         "cost by model for the deployment gate. Created by "
                         "setup_dashboards.py.")

WIDGETS = [
    {"name": "Gate: avg scores over time",
     "description": "Average of every numeric score per day, one line per score name.",
     "view": "scores-numeric",
     "dimensions": [{"field": "name"}],
     "metrics": [{"measure": "value", "agg": "avg"}],
     "filters": [],
     "chartType": "LINE_TIME_SERIES"},
    {"name": "Gate: compliance true-rate",
     "description": "Share of outputs free of prohibited phrases "
                    "(regulatory_compliance is BOOLEAN; avg = true-rate).",
     "view": "scores-boolean",
     "dimensions": [],
     "metrics": [{"measure": "value", "agg": "avg"}],
     "filters": [{"column": "name", "operator": "=", "type": "string",
                  "value": "regulatory_compliance"}],
     "chartType": "LINE_TIME_SERIES"},
    {"name": "Gate: score distribution",
     "description": "Average value per numeric score name.",
     "view": "scores-numeric",
     "dimensions": [{"field": "name"}],
     "metrics": [{"measure": "value", "agg": "avg"}],
     "filters": [],
     "chartType": "VERTICAL_BAR"},
    {"name": "Gate: cost by model",
     "description": "Total cost by model.",
     "view": "observations",
     "dimensions": [{"field": "providedModelName"}],
     "metrics": [{"measure": "totalCost", "agg": "sum"}],
     "filters": [],
     "chartType": "HORIZONTAL_BAR"},
]

# 12-column grid: two widgets per row.
LAYOUT = [(0, 0), (6, 0), (0, 6), (6, 6)]


def placements(widget_ids: list) -> list:
    return [{"type": "widget", "widgetId": wid, "x": x, "y": y,
             "width": 6, "height": 6}
            for wid, (x, y) in zip(widget_ids, LAYOUT)]


def _send(method, path, body):
    host, auth = langfuse_creds()
    req = urllib.request.Request(
        f"{host}{path}", data=json.dumps(body).encode(), method=method,
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def _list_all(path):
    rows, page = [], 1
    while True:
        resp = api_get(path, {"page": page, "limit": 100})
        rows.extend(resp.get("data") or [])
        total = (resp.get("meta") or {}).get("totalPages") or 1
        if page >= total:
            return rows
        page += 1


def _placed_widget_ids(dashboard: dict) -> set:
    definition = dashboard.get("definition") or {}
    return {p.get("widgetId") for p in definition.get("widgets") or []
            if p.get("widgetId")}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Create the deployment-gate dashboard")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the payloads; create nothing")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    host, _ = langfuse_creds()
    print("Langfuse dashboard setup (unstable API)", file=sys.stderr)
    print("=" * 50, file=sys.stderr)
    print(f"  Target:    {host}", file=sys.stderr)
    print(f"  Dashboard: {DASHBOARD_NAME}", file=sys.stderr)

    if args.dry_run:
        print("\n  ** DRY RUN - nothing will be created **\n", file=sys.stderr)
        print(json.dumps({
            "widgets": WIDGETS,
            "dashboard": {"name": DASHBOARD_NAME, "description": DASHBOARD_DESCRIPTION},
            "placements": placements([f"<{w['name']}-id>" for w in WIDGETS]),
        }, indent=2))
        return 0

    try:
        existing_widgets = {w.get("name"): w
                            for w in _list_all("/api/public/unstable/dashboard-widgets")}
        widget_ids = []
        for widget in WIDGETS:
            if widget["name"] in existing_widgets:
                wid = existing_widgets[widget["name"]]["id"]
                print(f"  [skip] widget '{widget['name']}' (exists: {wid})",
                      file=sys.stderr)
            else:
                wid = _send("POST", "/api/public/unstable/dashboard-widgets", widget)["id"]
                print(f"  [created] widget '{widget['name']}' (id: {wid})",
                      file=sys.stderr)
            widget_ids.append(wid)

        dashboards = {d.get("name"): d
                      for d in _list_all("/api/public/unstable/dashboards")}
        dashboard = dashboards.get(DASHBOARD_NAME)
        if dashboard:
            print(f"  [skip] dashboard (exists: {dashboard['id']})", file=sys.stderr)
            dashboard = api_get(f"/api/public/unstable/dashboards/{dashboard['id']}")
        else:
            dashboard = _send("POST", "/api/public/unstable/dashboards",
                              {"name": DASHBOARD_NAME,
                               "description": DASHBOARD_DESCRIPTION})
            print(f"  [created] dashboard (id: {dashboard['id']})", file=sys.stderr)

        placed = _placed_widget_ids(dashboard)
        for placement in placements(widget_ids):
            if placement["widgetId"] in placed:
                continue
            _send("POST", f"/api/public/unstable/dashboards/{dashboard['id']}/placements",
                  placement)
            print(f"  [placed] widget {placement['widgetId']} at "
                  f"({placement['x']},{placement['y']})", file=sys.stderr)
    except urllib.error.HTTPError as e:
        print(f"  [error] {e.code} {e.read().decode()}", file=sys.stderr)
        return 1

    print(f"\n  Open: {host}/project/<project>/dashboards", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
