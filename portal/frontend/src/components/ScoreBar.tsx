interface Props {
  value: number | null | undefined;
  /** The bar this score is judged against. Null/undefined means no bar was
   *  recorded, so the fill stays neutral instead of implying a verdict. */
  threshold?: number | null;
  /** A verdict decided server-side against the unrounded mean. When given it
   *  wins over comparing `value` (which may be a rounded display value). */
  cleared?: boolean | null;
}

export default function ScoreBar({ value, threshold, cleared }: Props) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="score-pill mute">—</span>;
  }
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const passed =
    cleared != null ? cleared : threshold != null ? value >= threshold : null;
  const verdict = passed === null ? "neutral" : passed ? "pass" : "fail";
  return (
    <div className="score-bar">
      <div className="score-bar-track">
        <div
          className={`score-bar-fill ${verdict}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="score-bar-value">{pct.toFixed(1)}%</span>
    </div>
  );
}
