import type { GateThresholds } from "../types";
import { metricLabel } from "./format";

/** 0.8 → "80%". Bars are authored as whole percents. */
export function barPct(bar: number): string {
  return `${Math.round(bar * 100)}%`;
}

/** Gate dimensions in stable alphabetical order. */
export function gateEntries(gate: GateThresholds): [string, number][] {
  return Object.entries(gate).sort(([a], [b]) => a.localeCompare(b));
}

/**
 * "completeness ≥ 70% · groundedness ≥ 80% · …" — the whole gate spelled out,
 * for the tooltip on a cell that only has room for one of the bars.
 */
export function gateSummary(gate: GateThresholds): string {
  const dims = gateEntries(gate)
    .map(([name, bar]) => `${metricLabel(name)} ≥ ${barPct(bar)}`)
    .join(" · ");
  return `Every dimension must clear its own bar: ${dims}`;
}
