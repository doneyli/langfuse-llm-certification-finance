export type CertStatus = "PASSED" | "FAILED" | "UNKNOWN";

/**
 * The run's primary score, picked server-side via a fallback chain
 * (avg_numerical_accuracy → avg_sentiment_accuracy → avg_groundedness →
 * avg_exact_match → avg_completeness → first other avg_*). `name` says which
 * metric the value is; both are null when the run emitted no avg_* score.
 */
export interface PrimaryScore {
  name: string | null;
  value: number | null;
}

/**
 * An agent gate's per-dimension bars, e.g.
 * `{ groundedness: 0.8, regulatory_compliance: 1.0 }`. Every dimension must
 * clear its own bar, so this never collapses into a single number. Model
 * certification runs have no gate dict — they carry a scalar `threshold`.
 */
export type GateThresholds = Record<string, number>;

export interface DashboardRow {
  model: string;
  dataset: string;
  dataset_short: string;
  status: CertStatus;
  primary_score: PrimaryScore;
  /** Bar for `primary_score` — the model scalar, or the matching gate
   *  dimension's bar; null when the run recorded no bar for that score. */
  threshold: number | null;
  gate_thresholds: GateThresholds | null;
  run_name: string;
  timestamp: string;
  cert_comment: string;
}

export interface HistoryRun {
  run_name: string;
  model: string;
  status: CertStatus;
  primary_score: PrimaryScore;
  /** Bar for `primary_score`; see `DashboardRow.threshold`. */
  threshold: number | null;
  gate_thresholds: GateThresholds | null;
  timestamp: string;
  cert_comment: string;
}

export interface ScoreAggregate {
  /** Rounded for display — never compare it to `bar`; read `cleared`. */
  mean: number;
  min: number;
  max: number;
  count: number;
  pass_rate: number;
  /** The bar this evaluator was judged against; null if the gate did not
   *  judge it (a model gate judges one score, never the rest). */
  bar: number | null;
  /** Whether the unrounded mean cleared `bar`, decided server-side; null when
   *  there is no bar. */
  cleared: boolean | null;
}

export interface RunItem {
  trace_id: string;
  input: Record<string, unknown> | string;
  expected_output: Record<string, unknown> | string;
  question: string;
  expected_short: string;
  scores: Record<string, { value: number | null; comment: string }>;
}

export interface RunDetail {
  dataset: string;
  dataset_short: string;
  run_name: string;
  model: string;
  /** Scalar bar of a model gate; null for agent runs (see gate_thresholds). */
  threshold: number | null;
  gate_thresholds: GateThresholds | null;
  status: CertStatus;
  total_items: number;
  aggregates: Record<string, ScoreAggregate>;
  items: RunItem[];
  score_names: string[];
  langfuse_url: string;
  error?: string;
}
