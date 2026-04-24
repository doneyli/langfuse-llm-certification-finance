import { useAppTheme } from "./theme";

export interface ChartTheme {
  grid: string;
  axis: string;
  axisText: string;
  line: string;
  threshold: string;
  pass: string;
  fail: string;
  tooltipBg: string;
  tooltipBorder: string;
  tooltipText: string;
}

const LIGHT: ChartTheme = {
  grid: "rgba(15, 20, 25, 0.06)",
  axis: "rgba(15, 20, 25, 0.12)",
  axisText: "#687182",
  line: "#1e88e5",
  threshold: "#0a8a5f",
  pass: "#0a8a5f",
  fail: "#c42b2b",
  tooltipBg: "#ffffff",
  tooltipBorder: "#e3e5ea",
  tooltipText: "#0f1419",
};

const DARK: ChartTheme = {
  grid: "rgba(229, 231, 235, 0.08)",
  axis: "rgba(229, 231, 235, 0.14)",
  axisText: "#8892a4",
  line: "#60a5fa",
  threshold: "#34d399",
  pass: "#34d399",
  fail: "#f87171",
  tooltipBg: "#13161d",
  tooltipBorder: "#232731",
  tooltipText: "#e5e7eb",
};

export function useChartTheme(): ChartTheme {
  const { theme } = useAppTheme();
  return theme === "dark" ? DARK : LIGHT;
}
