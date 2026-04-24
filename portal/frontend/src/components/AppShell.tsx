import type { ReactNode } from "react";
import { Link as RouterLink, useLocation } from "react-router-dom";
import {
  SidebarNavigationItem,
  SidebarNavigationTitle,
} from "@clickhouse/click-ui";

import { useConfig } from "../lib/config";
import { DATASETS } from "../lib/datasets";
import { useAppTheme } from "../lib/theme";
import ThemeToggle from "./ThemeToggle";

interface Props {
  children: ReactNode;
}

export default function AppShell({ children }: Props) {
  const { pathname } = useLocation();
  const { theme } = useAppTheme();

  const isDashboard = pathname === "/";
  const langfuseLogo =
    theme === "dark" ? "/langfuse-wordmark-white.svg" : "/langfuse-wordmark.svg";

  return (
    <div className="app">
      <aside className="sidebar">
        <RouterLink to="/" className="brand">
          <div className="brand-lockup">
            <img
              src={langfuseLogo}
              alt="Langfuse"
              className="brand-langfuse"
              draggable={false}
            />
            <div className="brand-by">
              <span className="brand-by-word">by</span>
              <span className="brand-ch" aria-label="ClickHouse">
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 36 36"
                  fill="none"
                  aria-hidden
                >
                  <rect x="0" y="2" width="4" height="32" rx="1" fill="currentColor" />
                  <rect x="8" y="2" width="4" height="32" rx="1" fill="currentColor" />
                  <rect x="16" y="2" width="4" height="32" rx="1" fill="currentColor" />
                  <rect x="24" y="2" width="4" height="32" rx="1" fill="currentColor" />
                  <rect x="32" y="15" width="4" height="8" rx="1" fill="currentColor" />
                </svg>
                <span className="brand-ch-word">ClickHouse</span>
              </span>
            </div>
          </div>
          <span className="brand-sub">Certification Portal</span>
        </RouterLink>

        <div className="sidebar-section">
          <SidebarNavigationTitle label="Overview" />
          <RouterLink to="/" style={{ textDecoration: "none" }}>
            <SidebarNavigationItem
              label="Dashboard"
              icon="cards"
              selected={isDashboard}
            />
          </RouterLink>
        </div>

        <div className="sidebar-section">
          <SidebarNavigationTitle label="Datasets" />
          {DATASETS.map((d) => {
            const href = `/history/${d.slug}`;
            const active = pathname.includes(`/${d.slug}`);
            return (
              <RouterLink
                key={d.slug}
                to={href}
                style={{ textDecoration: "none" }}
              >
                <SidebarNavigationItem
                  label={d.label}
                  icon={d.kind === "numerical" ? "bar-chart" : "chat"}
                  selected={active}
                />
              </RouterLink>
            );
          })}
        </div>

        <div className="sidebar-section">
          <SidebarNavigationTitle label="Source of truth" />
          <LangfuseSourceLink />
        </div>

        <div className="sidebar-footer">
          <ThemeToggle />
        </div>
      </aside>

      <main className="main">{children}</main>
    </div>
  );
}

function LangfuseSourceLink() {
  const { langfuse_url } = useConfig();
  return (
    <a
      href={langfuse_url}
      target="_blank"
      rel="noopener noreferrer"
      className="source-link"
    >
      <span className="source-link-dot" />
      <span className="source-link-text">
        <span className="source-link-title">Open Langfuse</span>
        <span className="source-link-sub">Full eval traces &amp; data</span>
      </span>
      <svg
        width="12"
        height="12"
        viewBox="0 0 16 16"
        fill="none"
        aria-hidden
        className="source-link-arrow"
      >
        <path
          d="M6 3h7v7M13 3L3 13"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </a>
  );
}
