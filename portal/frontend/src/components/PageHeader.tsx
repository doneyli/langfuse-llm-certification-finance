import type { ReactNode } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Text, Title } from "@clickhouse/click-ui";

export interface Crumb {
  label: string;
  to?: string;
}

interface Props {
  crumbs?: Crumb[];
  title: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
}

export default function PageHeader({ crumbs, title, subtitle, actions }: Props) {
  return (
    <header className="page-header">
      {crumbs && crumbs.length > 0 && (
        <nav className="crumbs" aria-label="Breadcrumb">
          {crumbs.map((c, i) => (
            <span key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              {c.to ? <RouterLink to={c.to}>{c.label}</RouterLink> : <span>{c.label}</span>}
              {i < crumbs.length - 1 && <span className="crumbs-sep">/</span>}
            </span>
          ))}
        </nav>
      )}
      <div className="page-header-row">
        <div style={{ flex: 1, minWidth: 0 }}>
          <Title type="h1" size="xl">
            {title}
          </Title>
          {subtitle && (
            <div style={{ marginTop: 4 }}>
              {typeof subtitle === "string" ? (
                <Text color="muted" size="md">
                  {subtitle}
                </Text>
              ) : (
                subtitle
              )}
            </div>
          )}
        </div>
        {actions && <div>{actions}</div>}
      </div>
    </header>
  );
}
