interface Props {
  value: number | null | undefined;
  threshold?: number;
}

export default function ScoreBar({ value, threshold = 0.5 }: Props) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="score-pill mute">—</span>;
  }
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const pass = value >= threshold;
  return (
    <div className="score-bar">
      <div className="score-bar-track">
        <div
          className={`score-bar-fill ${pass ? "pass" : "fail"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="score-bar-value">{pct.toFixed(1)}%</span>
    </div>
  );
}
