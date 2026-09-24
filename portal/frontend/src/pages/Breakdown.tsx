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
import { barPct } from "../lib/gate";
import type { ScoreAggregate } from "../types";

// Every evaluator row carries the bar it was judged against, decided
// server-side: an agent gate's bar for that dimension, a model gate's one bar
// on its one judged score only, and "—" for any evaluator the gate never
// judged. So the column is truthful for both gate kinds.
const HEADERS: TableColumnConfigProps[] = [
  { label: "Evaluator" },
  { label: "Gate", width: "90px" },
  { label: "Mean", width: "200px" },
  { label: "Min", width: "90px" },
  { label: "Max", width: "90px" },
  { label: "Pass rate", width: "110px" },
  { label: "Items", width: "90px" },
];

/** Read a bar/verdict defensively: a stale payload may omit the keys. */
function barOf(agg: ScoreAggregate): number | null {
  return agg.bar ?? null;
}
function clearedOf(agg: ScoreAggregate): boolean | null {
  return agg.cleared ?? null;
}

function aggRow([name, agg]: [string, ScoreAggregate]): TableRowType {
  const bar = barOf(agg);
  return {
    id: name,
    items: [
      { label: <span style={{ fontWeight: 600 }}>{name}</span> },
      {
        label: (
          <span
            className="mono"
            style={{
              fontSize: 13,
              color: bar === null ? "var(--text-subtle)" : "var(--text-muted)",
            }}
          >
            {bar === null ? "—" : `≥ ${barPct(bar)}`}
          </span>
        ),
      },
      {
        label: (
          <ScoreBar value={agg.mean} threshold={bar} cleared={clearedOf(agg)} />
        ),
      },
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
        // A model gate is one bar on one judged score; an agent gate is one
        // bar per dimension, all of which must clear. `?? null` because a
        // stale payload can omit these keys, and undefined must mean "none".
        const gate = data.gate_thresholds ?? null;
        const scalar = data.threshold ?? null;
        const aggEntries = Object.entries(data.aggregates).sort(([a], [b]) =>
          a.localeCompare(b)
        );
        // The evaluator(s) a model gate's single bar actually judged.
        const judged = aggEntries
          .filter(([, agg]) => barOf(agg) !== null)
          .map(([name]) => name);

        const chartData = aggEntries.map(([name, agg]) => ({
          name,
          mean: agg.mean * 100,
          bar: barOf(agg),
          cleared: clearedOf(agg),
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
              {gate !== null ? (
                <BigStat
                  label="Gate (all must clear)"
                  title={`${Object.keys(gate).length} dims`}
                  size="lg"
                  state="muted"
                />
              ) : (
                <BigStat
                  label={
                    judged.length > 0 ? `Threshold · ${judged.join(", ")}` : "Threshold"
                  }
                  title={scalar !== null ? barPct(scalar) : "—"}
                  size="lg"
                  state="muted"
                />
              )}
              {aggEntries.map(([name, agg]) => {
                const bar = barOf(agg);
                return (
                  <BigStat
                    key={name}
                    label={
                      bar === null ? (
                        name
                      ) : (
                        <>
                          {name}{" "}
                          <span style={{ color: "var(--text-muted)" }}>
                            ≥ {barPct(bar)}
                          </span>
                        </>
                      )
                    }
                    title={`${(agg.mean * 100).toFixed(1)}%`}
                    size="lg"
                    error={clearedOf(agg) === false}
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
                      {/* No reference line: no single bar applies to every
                          evaluator (a model gate's bar judges one score), so a
                          line across all of them would imply bars that don't
                          exist. Each bar's own gate is in its tooltip, its
                          stat card, and the Gate column below. */}
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
                          const bar =
                            (item?.payload as { bar?: number | null } | undefined)
                              ?.bar ?? null;
                          return [
                            bar === null
                              ? `${v.toFixed(1)}% (not gated)`
                              : `${v.toFixed(1)}% (gate ≥ ${barPct(bar)})`,
                            "Mean",
                          ];
                        }}
                      />
                      <Bar dataKey="mean" radius={[4, 4, 0, 0]}>
                        {chartData.map((d) => (
                          <Cell
                            key={d.name}
                            fill={
                              d.cleared === null
                                ? chart.neutral
                                : d.cleared
                                  ? chart.pass
                                  : chart.fail
                            }
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
                headers={HEADERS}
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
