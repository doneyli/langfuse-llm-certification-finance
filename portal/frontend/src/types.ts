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

export interface DashboardRow {
  model: string;
  dataset: string;
  dataset_short: string;
  status: CertStatus;
  primary_score: PrimaryScore;
  threshold: number;
  run_name: string;
  timestamp: string;
  cert_comment: string;
}

export interface HistoryRun {
  run_name: string;
  model: string;
  status: CertStatus;
  primary_score: PrimaryScore;
  threshold: number;
  timestamp: string;
  cert_comment: string;
}

export interface ScoreAggregate {
  mean: number;
  min: number;
  max: number;
  count: number;
  pass_rate: number;
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
  threshold: number;
  status: CertStatus;
  total_items: number;
  aggregates: Record<string, ScoreAggregate>;
  items: RunItem[];
  score_names: string[];
  langfuse_url: string;
  error?: string;
}
