import type { AdvisoryThresholds, GateThresholds } from "../types";
import { metricLabel } from "./format";

/** 0.8 → "80%". Bars are authored as whole percents. */
export function barPct(bar: number): string {
  return `${Math.round(bar * 100)}%`;
}

/** Gate dimensions in stable alphabetical order. */
export function gateEntries(gate: GateThresholds): [string, number][] {
  return Object.entries(gate).sort(([a], [b]) => a.localeCompare(b));
}

function barList(bars: GateThresholds): string {
  return gateEntries(bars)
    .map(([name, bar]) => `${metricLabel(name)} ≥ ${barPct(bar)}`)
    .join(" · ");
}

/**
 * "groundedness ≥ 80% · regulatory compliance ≥ 100% · …": the whole gate
 * spelled out, for the tooltip on a cell that only has room for one of the
 * bars. Advisory bars are listed after it, labelled as never blocking.
 */
export function gateSummary(
  gate: GateThresholds,
  advisory?: AdvisoryThresholds | null
): string {
  const required = `Every dimension must clear its own bar: ${barList(gate)}`;
  if (advisory == null || Object.keys(advisory).length === 0) return required;
  return `${required}. Advisory, reported but never blocks: ${barList(advisory)}`;
}
