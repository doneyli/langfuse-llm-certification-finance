import { Link as RouterLink, useParams } from "react-router-dom";
import {
  Link,
  Panel,
  Table,
  Title,
  type TableColumnConfigProps,
  type TableRowType,
} from "@clickhouse/click-ui";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { AsyncView, useAsync } from "../components/AsyncView";
import PageHeader from "../components/PageHeader";
import ProvenanceStrip from "../components/ProvenanceStrip";
import ScoreBar from "../components/ScoreBar";
import StatusBadge from "../components/StatusBadge";
import ThresholdCell from "../components/ThresholdCell";
import { api } from "../lib/api";
import { useChartTheme } from "../lib/chartTheme";
import { datasetLabel } from "../lib/datasets";
import { datetime, metricLabel, shortDate } from "../lib/format";
import { barPct } from "../lib/gate";
import type { HistoryRun } from "../types";

const headers: TableColumnConfigProps[] = [
  { label: "Run", width: "280px" },
  { label: "Model" },
  { label: "Status" },
  { label: "Score" },
  { label: "Threshold" },
  { label: "Date" },
  { label: "", width: "100px" },
];

function tableRow(dataset: string, run: HistoryRun): TableRowType {
  const detailsHref = `/breakdown/${dataset}/${encodeURIComponent(run.run_name)}`;
  return {
    id: run.run_name,
    items: [
      {
        label: (
          <span
            className="mono truncate"
            title={run.run_name}
            style={{ fontSize: 12, color: "var(--text-muted)" }}
          >
            {run.run_name}
          </span>
        ),
      },
      { label: <span style={{ fontWeight: 600 }}>{run.model}</span> },
      { label: <StatusBadge status={run.status} /> },
      {
        label: (
          <div
            style={{ display: "flex", flexDirection: "column", gap: 2 }}
            title={run.primary_score.name ?? undefined}
          >
            <ScoreBar
              value={run.primary_score.value}
              threshold={run.threshold}
            />
            {run.primary_score.name && (
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                {metricLabel(run.primary_score.name)}
              </span>
            )}
          </div>
        ),
      },
      {
        label: (
          <ThresholdCell
            threshold={run.threshold}
            gate={run.gate_thresholds}
            detailsHref={detailsHref}
          />
        ),
      },
      {
        label: (
          <span className="mono" style={{ fontSize: 13, color: "var(--text-muted)" }}>
            {datetime(run.timestamp)}
          </span>
        ),
      },
      {
        label: (
          <Link
            component={RouterLink}
            size="sm"
            weight="medium"
            to={detailsHref}
          >
            Details
          </Link>
        ),
      },
    ],
  };
}

export default function History() {
  const { dataset: d1, sub } = useParams<{ dataset: string; sub: string }>();
  const dataset = `${d1}/${sub}`;
  const state = useAsync(() => api.history(dataset), [dataset]);
  const chart = useChartTheme();

  return (
    <>
      <ProvenanceStrip />
      <PageHeader
        crumbs={[
          { label: "Dashboard", to: "/" },
          { label: datasetLabel(dataset) },
          { label: "History" },
        ]}
        title="Run history"
        subtitle={dataset}
      />

      <AsyncView state={state}>
        {(runs) => {
          const chartData = [...runs]
            .reverse()
            .filter((r) => r.primary_score.value !== null)
            .map((r) => ({
              date: shortDate(r.timestamp),
              score:
                r.primary_score.value !== null
                  ? r.primary_score.value * 100
                  : null,
              model: r.model,
              metric: metricLabel(r.primary_score.name),
              bar: r.threshold ?? null,
            }));

          // A dataset's history mixes models and agents, each plotted on its
          // own primary score and judged against its own bar. One reference
          // line is only honest when every plotted run shares the same metric
          // and the same bar; otherwise each point's bar is in its tooltip.
          const first = chartData[0];
          const sharedBar =
            first &&
            first.bar !== null &&
            chartData.every((d) => d.metric === first.metric && d.bar === first.bar)
              ? first.bar
              : null;
          const barLabel =
            sharedBar !== null
              ? `${first.metric || "score"} ≥ ${barPct(sharedBar)}`
              : "";

          return (
            <>
              {chartData.length > 0 && (
                <Panel
                  hasBorder
                  radii="md"
                  padding="md"
                  color="default"
                  className="section"
                >
                  <div style={{ marginBottom: 12 }}>
                    <Title type="h3" size="sm">
                      Score trend
                    </Title>
                  </div>
                  <div style={{ width: "100%", height: 260 }}>
                    <ResponsiveContainer>
                      <LineChart
                        data={chartData}
                        margin={{ top: 10, right: 16, bottom: 8, left: 0 }}
                      >
                        <CartesianGrid
                          stroke={chart.grid}
                          strokeDasharray="3 3"
                          vertical={false}
                        />
                        <XAxis
                          dataKey="date"
                          stroke={chart.axisText}
                          fontSize={11}
                          tickLine={false}
                          axisLine={{ stroke: chart.axis }}
                        />
                        <YAxis
                          stroke={chart.axisText}
                          fontSize={11}
                          tickLine={false}
                          axisLine={false}
                          domain={[0, 100]}
                          tickFormatter={(v) => `${v}%`}
                        />
                        {sharedBar !== null && (
                          <ReferenceLine
                            y={sharedBar * 100}
                            stroke={chart.threshold}
                            strokeDasharray="4 4"
                            label={{
                              value: barLabel,
                              fill: chart.threshold,
                              fontSize: 11,
                              position: "insideTopRight",
                            }}
                          />
                        )}
                        <Tooltip
                          contentStyle={{
                            background: chart.tooltipBg,
                            border: `1px solid ${chart.tooltipBorder}`,
                            borderRadius: 6,
                            fontSize: 12,
                            color: chart.tooltipText,
                          }}
                          labelStyle={{ color: chart.tooltipText }}
                          itemStyle={{ color: chart.tooltipText }}
                          formatter={(v: number, _name, item) => {
                            const p = item?.payload as
                              | { metric?: string; bar?: number | null; model?: string }
                              | undefined;
                            const bar = p?.bar ?? null;
                            return [
                              bar === null
                                ? `${v.toFixed(1)}% (no bar recorded)`
                                : `${v.toFixed(1)}% (bar ≥ ${barPct(bar)})`,
                              `${p?.model ?? "run"} · ${p?.metric || "score"}`,
                            ];
                          }}
                        />
                        <Line
                          type="monotone"
                          dataKey="score"
                          stroke={chart.line}
                          strokeWidth={2}
                          dot={{ r: 3, fill: chart.line }}
                          activeDot={{ r: 5 }}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </Panel>
              )}

              <Panel
                padding="none"
                hasBorder
                radii="md"
                color="default"
                className="section"
              >
                <Table
                  headers={headers}
                  rows={runs.map((r) => tableRow(dataset, r))}
                  size="md"
                  noDataMessage="No runs yet."
                />
              </Panel>
            </>
          );
        }}
      </AsyncView>
    </>
  );
}
