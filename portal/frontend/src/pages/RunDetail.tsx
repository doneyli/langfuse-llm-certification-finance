import { useParams } from "react-router-dom";
import {
  Link,
  Panel,
  Table,
  type TableColumnConfigProps,
  type TableRowType,
} from "@clickhouse/click-ui";

import { AsyncView, useAsync } from "../components/AsyncView";
import PageHeader from "../components/PageHeader";
import ProvenanceStrip from "../components/ProvenanceStrip";
import StatusBadge from "../components/StatusBadge";
import { api } from "../lib/api";
import { datasetLabel } from "../lib/datasets";
import type { RunItem } from "../types";

function ScoreCell({
  score,
}: {
  score: { value: number | null; comment: string } | undefined;
}) {
  if (!score || score.value === null || score.value === undefined) {
    return <span className="score-pill mute">—</span>;
  }
  const pct = Math.round(score.value * 100);
  const cls = score.value === 0 ? "fail" : score.value >= 0.5 ? "pass" : "fail";
  return (
    <span className={`score-pill ${cls}`} title={score.comment || undefined}>
      {pct}%
    </span>
  );
}

export default function RunDetail() {
  const { dataset: d1, sub, runName } = useParams<{
    dataset: string;
    sub: string;
    runName: string;
  }>();
  const dataset = `${d1}/${sub}`;
  const run = runName ?? "";
  const state = useAsync(() => api.run(dataset, run), [dataset, run]);

  return (
    <AsyncView state={state}>
      {(data) => {
        const headers: TableColumnConfigProps[] = [
          { label: "#", width: "48px" },
          { label: "Question" },
          { label: "Expected", width: "200px" },
          ...data.score_names.map((n) => ({
            label: n,
            width: "110px",
          })),
          { label: "Trace", width: "80px" },
        ];

        const rows: TableRowType[] = data.items.map((item, idx) =>
          itemRow(item, idx, data.score_names, data.langfuse_url)
        );

        return (
          <>
            <ProvenanceStrip deepLink={`${data.langfuse_url}/trace`} />
            <PageHeader
              crumbs={[
                { label: "Dashboard", to: "/" },
                { label: datasetLabel(dataset), to: `/history/${dataset}` },
                {
                  label: data.model,
                  to: `/breakdown/${dataset}/${encodeURIComponent(run)}`,
                },
                { label: "Items" },
              ]}
              title={
                <span style={{ display: "inline-flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  Per-item scores
                  <StatusBadge status={data.status} size="md" />
                </span>
              }
              subtitle={
                <span style={{ color: "var(--text-muted)" }}>
                  {data.model} &middot;{" "}
                  <span className="mono" style={{ fontSize: 12 }}>
                    {data.run_name}
                  </span>{" "}
                  &middot; {data.total_items} items
                </span>
              }
            />

            <Panel
              padding="none"
              hasBorder
              radii="md"
              color="default"
              className="section"
            >
              <Table
                headers={headers}
                rows={rows}
                size="md"
                noDataMessage="No per-item data available."
              />
            </Panel>
          </>
        );
      }}
    </AsyncView>
  );
}

function itemRow(
  item: RunItem,
  idx: number,
  scoreNames: string[],
  langfuseUrl: string
): TableRowType {
  return {
    id: item.trace_id || String(idx),
    items: [
      {
        label: (
          <span className="mono" style={{ color: "var(--text-muted)", fontSize: 12 }}>
            {idx + 1}
          </span>
        ),
      },
      {
        label: (
          <span className="truncate" title={item.question}>
            {item.question || "—"}
          </span>
        ),
      },
      {
        label: (
          <span
            className="mono truncate"
            title={item.expected_short}
            style={{ fontSize: 12, color: "var(--text-muted)" }}
          >
            {item.expected_short || "—"}
          </span>
        ),
      },
      ...scoreNames.map((n) => ({
        label: <ScoreCell score={item.scores[n]} />,
      })),
      {
        label: item.trace_id ? (
          <Link
            size="sm"
            weight="medium"
            icon="popout"
            onClick={() =>
              window.open(
                `${langfuseUrl}/trace/${item.trace_id}`,
                "_blank",
                "noopener,noreferrer"
              )
            }
          >
            Trace
          </Link>
        ) : (
          <span className="score-pill mute">—</span>
        ),
      },
    ],
  };
}
