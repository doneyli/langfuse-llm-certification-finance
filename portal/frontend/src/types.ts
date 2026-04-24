export type CertStatus = "PASSED" | "FAILED" | "UNKNOWN";

export interface DashboardRow {
  model: string;
  dataset: string;
  dataset_short: string;
  status: CertStatus;
  primary_score: number | null;
  primary_name: string;
  threshold: number;
  run_name: string;
  timestamp: string;
  cert_comment: string;
}

export interface HistoryRun {
  run_name: string;
  model: string;
  status: CertStatus;
  primary_score: number | null;
  primary_name: string;
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
