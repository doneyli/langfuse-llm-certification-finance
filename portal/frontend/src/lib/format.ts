import type { BadgeState } from "@clickhouse/click-ui";
import type { CertStatus } from "../types";

export function statusToBadge(status: CertStatus): {
  state: BadgeState;
  text: string;
} {
  switch (status) {
    case "PASSED":
      return { state: "success", text: "Passed" };
    case "FAILED":
      return { state: "danger", text: "Failed" };
    default:
      return { state: "neutral", text: "Pending" };
  }
}

export function pct(n: number | null | undefined, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

export function pctRound(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${Math.round(n * 100)}%`;
}

/** "avg_numerical_accuracy" → "numerical accuracy" (for secondary text). */
export function metricLabel(name: string | null | undefined): string {
  if (!name) return "";
  return name.replace(/^avg_/, "").replace(/_/g, " ");
}

export function shortDate(ts: string | undefined): string {
  if (!ts) return "—";
  return ts.slice(0, 10);
}

export function datetime(ts: string | undefined): string {
  if (!ts) return "—";
  return ts.slice(0, 19).replace("T", " ");
}
