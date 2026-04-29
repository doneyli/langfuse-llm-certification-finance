import { useState } from "react";
import { Link as RouterLink } from "react-router-dom";
import {
  BigStat,
  Button,
  Link,
  Panel,
  Table,
  Text,
  type TableColumnConfigProps,
  type TableRowType,
} from "@clickhouse/click-ui";

import { AsyncView, useAsync } from "../components/AsyncView";
import PageHeader from "../components/PageHeader";
import ProvenanceStrip from "../components/ProvenanceStrip";
import ScoreBar from "../components/ScoreBar";
import StatusBadge from "../components/StatusBadge";
import { api } from "../lib/api";
import { datasetLabel } from "../lib/datasets";
import { shortDate } from "../lib/format";
import type { CertStatus, DashboardRow } from "../types";

type Filter = "ALL" | CertStatus;

const headers: TableColumnConfigProps[] = [
  { label: "Model" },
  { label: "Dataset" },
  { label: "Status" },
  { label: "Primary score" },
  { label: "Threshold" },
  { label: "Last run" },
  { label: "", width: "160px" },
];

function tableRow(row: DashboardRow): TableRowType {
  return {
    id: `${row.dataset}::${row.run_name}`,
    items: [
      { label: <span style={{ fontWeight: 600 }}>{row.model}</span> },
      { label: datasetLabel(row.dataset) },
      { label: <StatusBadge status={row.status} /> },
      {
        label: (
          <ScoreBar value={row.primary_score} threshold={row.threshold} />
        ),
      },
      {
        label: (
          <span className="mono" style={{ fontSize: 13 }}>
            {Math.round(row.threshold * 100)}%
          </span>
        ),
      },
      {
        label: (
          <span className="mono" style={{ color: "var(--text-muted)", fontSize: 13 }}>
            {shortDate(row.timestamp)}
          </span>
        ),
      },
      {
        label: (
          <span className="row-actions">
            <Link
              component={RouterLink}
              size="sm"
              weight="medium"
              to={`/breakdown/${row.dataset}/${encodeURIComponent(row.run_name)}`}
            >
              Details
            </Link>
            <span style={{ color: "var(--stroke-strong)" }}>·</span>
            <Link
              component={RouterLink}
              size="sm"
              weight="medium"
              to={`/history/${row.dataset}`}
            >
              History
            </Link>
          </span>
        ),
      },
    ],
  };
}

interface FilterStatProps {
  label: string;
  count: number;
  filter: Filter;
  active: Filter;
  onSelect: (f: Filter) => void;
  variant?: "default" | "danger" | "muted";
}

function FilterStat({
  label,
  count,
  filter,
  active,
  onSelect,
  variant = "default",
}: FilterStatProps) {
  const isActive = active === filter;
  return (
    <button
      type="button"
      role="radio"
      aria-checked={isActive}
      aria-label={`Filter: ${label} (${count})`}
      onClick={() => onSelect(filter)}
      className={`stat-filter stat-filter-${variant} ${isActive ? "active" : ""}`}
    >
      <BigStat
        label={label}
        title={String(count)}
        size="lg"
        state={variant === "muted" ? "muted" : "default"}
        error={variant === "danger" && count > 0}
      />
    </button>
  );
}

export default function Dashboard() {
  const state = useAsync(() => api.dashboard(), []);
  const [filter, setFilter] = useState<Filter>("ALL");

  return (
    <>
      <ProvenanceStrip />
      <PageHeader
        title="Certification Dashboard"
        subtitle="Model certification status against golden financial datasets"
        actions={
          <Button
            type="secondary"
            iconLeft="refresh"
            onClick={() => window.location.reload()}
          >
            Refresh
          </Button>
        }
      />
      <AsyncView state={state}>
        {(rows) => {
          const passed = rows.filter((r) => r.status === "PASSED").length;
          const failed = rows.filter((r) => r.status === "FAILED").length;
          const pending = rows.filter((r) => r.status === "UNKNOWN").length;
          const total = rows.length;
          const passRate = total > 0 ? ((passed / total) * 100).toFixed(0) : "—";

          const filteredRows =
            filter === "ALL" ? rows : rows.filter((r) => r.status === filter);

          return (
            <>
              <div className="stat-grid" role="radiogroup" aria-label="Filter rows by status">
                <FilterStat
                  label="Total evaluations"
                  count={total}
                  filter="ALL"
                  active={filter}
                  onSelect={setFilter}
                />
                <FilterStat
                  label="Certified"
                  count={passed}
                  filter="PASSED"
                  active={filter}
                  onSelect={setFilter}
                />
                <FilterStat
                  label="Failed"
                  count={failed}
                  filter="FAILED"
                  active={filter}
                  onSelect={setFilter}
                  variant="danger"
                />
                <BigStat
                  label="Pass rate"
                  title={total > 0 ? `${passRate}%` : "—"}
                  size="lg"
                  state={total > 0 ? "default" : "muted"}
                />
                <FilterStat
                  label="Pending"
                  count={pending}
                  filter="UNKNOWN"
                  active={filter}
                  onSelect={setFilter}
                  variant="muted"
                />
              </div>

              <Panel
                padding="none"
                hasBorder
                radii="md"
                color="default"
                className="section"
              >
                {rows.length === 0 ? (
                  <div className="empty">
                    <Text color="muted" size="md">
                      No certification runs found.
                    </Text>
                    <div>
                      <code className="empty-code">python run_certification.py</code>
                    </div>
                  </div>
                ) : filteredRows.length === 0 ? (
                  <div className="empty">
                    <Text color="muted" size="md">
                      No rows match the {filterLabel(filter)} filter.
                    </Text>
                    <div style={{ marginTop: 8 }}>
                      <Button type="secondary" onClick={() => setFilter("ALL")}>
                        Show all
                      </Button>
                    </div>
                  </div>
                ) : (
                  <Table
                    headers={headers}
                    rows={filteredRows.map(tableRow)}
                    size="md"
                  />
                )}
              </Panel>
            </>
          );
        }}
      </AsyncView>
    </>
  );
}

function filterLabel(f: Filter): string {
  switch (f) {
    case "PASSED":
      return "Certified";
    case "FAILED":
      return "Failed";
    case "UNKNOWN":
      return "Pending";
    default:
      return "current";
  }
}
