import type { DashboardRow, HistoryRun, RunDetail } from "../types";

async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} — ${path}`);
  }
  return res.json() as Promise<T>;
}

export interface AppConfig {
  langfuse_url: string;
}

export const api = {
  config: () => getJSON<AppConfig>("/api/config"),
  dashboard: () => getJSON<DashboardRow[]>("/api/dashboard"),
  history: (dataset: string) =>
    getJSON<HistoryRun[]>(`/api/history/${encodeURI(dataset)}`),
  breakdown: (dataset: string, runName: string) =>
    getJSON<RunDetail>(`/api/breakdown/${encodeURI(dataset)}/${encodeURIComponent(runName)}`),
  run: (dataset: string, runName: string) =>
    getJSON<RunDetail>(`/api/run/${encodeURI(dataset)}/${encodeURIComponent(runName)}`),
};
