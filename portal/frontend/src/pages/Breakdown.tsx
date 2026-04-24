import { Link as RouterLink, useParams } from "react-router-dom";
import {
  BigStat,
  Button,
  Panel,
  Table,
  Title,
  type TableColumnConfigProps,
  type TableRowType,
} from "@clickhouse/click-ui";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
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
import { api } from "../lib/api";
import { useChartTheme } from "../lib/chartTheme";
import { datasetLabel } from "../lib/datasets";
import type { ScoreAggregate } from "../types";

const headers: TableColumnConfigProps[] = [
  { label: "Evaluator" },
  { label: "Mean", width: "200px" },
  { label: "Min", width: "90px" },
  { label: "Max", width: "90px" },
  { label: "Pass rate", width: "110px" },
  { label: "Items", width: "90px" },
];

function aggRow([name, agg]: [string, ScoreAggregate]): TableRowType {
  return {
    id: name,
    items: [
      { label: <span style={{ fontWeight: 600 }}>{name}</span> },
      { label: <ScoreBar value={agg.mean} /> },
      {
        label: (
          <span className="mono" style={{ fontSize: 13 }}>
            {(agg.min * 100).toFixed(1)}%
          </span>
        ),
      },
      {
        label: (
          <span className="mono" style={{ fontSize: 13 }}>
            {(agg.max * 100).toFixed(1)}%
          </span>
        ),
      },
      {
        label: (
          <span className="mono" style={{ fontSize: 13 }}>
            {(agg.pass_rate * 100).toFixed(1)}%
          </span>
        ),
      },
      {
        label: (
          <span className="mono" style={{ fontSize: 13, color: "var(--text-muted)" }}>
            {agg.count}
          </span>
        ),
      },
    ],
  };
}

export default function Breakdown() {
  const { dataset: d1, sub, runName } = useParams<{
    dataset: string;
    sub: string;
    runName: string;
  }>();
  const dataset = `${d1}/${sub}`;
  const run = runName ?? "";
  const state = useAsync(() => api.breakdown(dataset, run), [dataset, run]);
  const chart = useChartTheme();

  return (
    <AsyncView state={state}>
      {(data) => {
        const threshold = data.threshold ?? 0.85;
        const thresholdPct = Math.round(threshold * 100);
        const aggEntries = Object.entries(data.aggregates).sort(([a], [b]) =>
          a.localeCompare(b)
        );

        const chartData = aggEntries.map(([name, agg]) => ({
          name,
          mean: agg.mean * 100,
        }));

        const langfuseDeep = `${data.langfuse_url}/trace`;
        return (
          <>
            <ProvenanceStrip deepLink={langfuseDeep} />
            <PageHeader
              crumbs={[
                { label: "Dashboard", to: "/" },
                { label: datasetLabel(dataset), to: `/history/${dataset}` },
                { label: data.model },
              ]}
              title={
                <span style={{ display: "inline-flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  {data.model}
                  <StatusBadge status={data.status} size="md" />
                </span>
              }
              subtitle={
                <span
                  className="mono"
                  style={{ fontSize: 12, color: "var(--text-muted)" }}
                >
                  {data.run_name}
                </span>
              }
              actions={
                <RouterLink
                  to={`/run/${dataset}/${encodeURIComponent(run)}`}
                  style={{ textDecoration: "none" }}
                >
                  <Button type="primary" iconRight="arrow-right">
                    Per-item view
                  </Button>
                </RouterLink>
              }
            />

            <div className="stat-grid">
              <BigStat
                label="Items evaluated"
                title={String(data.total_items)}
                size="lg"
              />
              <BigStat
                label="Threshold"
                title={`${thresholdPct}%`}
                size="lg"
                state="muted"
              />
              {aggEntries.map(([name, agg]) => {
                const pct = (agg.mean * 100).toFixed(1);
                return (
                  <BigStat
                    key={name}
                    label={name}
                    title={`${pct}%`}
                    size="lg"
                    error={agg.mean < threshold}
                  />
                );
              })}
            </div>

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
                    Evaluator scores
                  </Title>
                </div>
                <div style={{ width: "100%", height: 260 }}>
                  <ResponsiveContainer>
                    <BarChart
                      data={chartData}
                      margin={{ top: 10, right: 16, bottom: 8, left: 0 }}
                    >
                      <CartesianGrid
                        stroke={chart.grid}
                        strokeDasharray="3 3"
                        vertical={false}
                      />
                      <XAxis
                        dataKey="name"
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
                      <ReferenceLine
                        y={thresholdPct}
                        stroke={chart.threshold}
                        strokeDasharray="4 4"
                      />
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
                        formatter={(v: number) => [`${v.toFixed(1)}%`, "Mean"]}
                      />
                      <Bar dataKey="mean" radius={[4, 4, 0, 0]}>
                        {chartData.map((d) => (
                          <Cell
                            key={d.name}
                            fill={d.mean >= thresholdPct ? chart.pass : chart.fail}
                          />
                        ))}
                      </Bar>
                    </BarChart>
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
                rows={aggEntries.map(aggRow)}
                size="md"
                noDataMessage="No evaluator scores recorded."
              />
            </Panel>
          </>
        );
      }}
    </AsyncView>
  );
}
