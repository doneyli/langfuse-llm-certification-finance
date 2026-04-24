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
import type { DashboardRow } from "../types";

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

export default function Dashboard() {
  const state = useAsync(() => api.dashboard(), []);

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

          return (
            <>
              <div className="stat-grid">
                <BigStat
                  label="Total evaluations"
                  title={String(total)}
                  size="lg"
                />
                <BigStat
                  label="Certified"
                  title={String(passed)}
                  size="lg"
                />
                <BigStat
                  label="Failed"
                  title={String(failed)}
                  size="lg"
                  error={failed > 0}
                />
                <BigStat
                  label="Pass rate"
                  title={total > 0 ? `${passRate}%` : "—"}
                  size="lg"
                  state={total > 0 ? "default" : "muted"}
                />
                <BigStat
                  label="Pending"
                  title={String(pending)}
                  size="lg"
                  state="muted"
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
                ) : (
                  <Table
                    headers={headers}
                    rows={rows.map(tableRow)}
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
