import { Link as RouterLink } from "react-router-dom";
import { Link } from "@clickhouse/click-ui";

import { barPct, gateEntries, gateSummary } from "../lib/gate";
import type { GateThresholds } from "../types";

interface Props {
  /** Bar for the score shown next to this cell. */
  threshold: number | null | undefined;
  /** Per-dimension bars when the run was judged by an agent gate. */
  gate: GateThresholds | null | undefined;
  /** The run's Details page, which lists every dimension against its bar. */
  detailsHref: string;
}

/**
 * The "Threshold" table cell.
 *
 * A model gate has one bar, so we print it. An agent gate has one bar per
 * dimension and *all* of them must clear, so printing a single number would
 * misstate what the gate enforced: we print the bar for the score shown in the
 * row and link the dimension count to Details, where every bar is listed. A
 * link rather than a tooltip, so the whole gate is reachable by touch,
 * keyboard, and screen reader — not only by hovering a mouse.
 */
export default function ThresholdCell({ threshold, gate, detailsHref }: Props) {
  // `== null` on purpose: a stale or partial payload can omit these keys, and
  // undefined must read as "no bar", not as a bar.
  if (gate != null) {
    const dims = gateEntries(gate).length;
    return (
      <span style={{ display: "inline-flex", flexDirection: "column", gap: 2 }}>
        <span className="mono" style={{ fontSize: 13 }}>
          {threshold != null ? barPct(threshold) : "—"}
        </span>
        <Link
          component={RouterLink}
          to={detailsHref}
          size="sm"
          title={gateSummary(gate)}
          aria-label={`of ${dims} gate dims — ${gateSummary(gate)}. View all bars.`}
        >
          of {dims} gate dims
        </Link>
      </span>
    );
  }

  if (threshold == null) {
    return (
      <span
        className="mono"
        style={{ fontSize: 13, color: "var(--text-subtle)" }}
        title="This run recorded no threshold for this score"
      >
        —
      </span>
    );
  }

  return (
    <span className="mono" style={{ fontSize: 13 }}>
      {barPct(threshold)}
    </span>
  );
}
