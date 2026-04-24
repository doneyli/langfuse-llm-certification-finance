import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import { api, type AppConfig } from "./api";

const DEFAULT_CONFIG: AppConfig = { langfuse_url: "https://cloud.langfuse.com" };

const ConfigContext = createContext<AppConfig>(DEFAULT_CONFIG);

export function ConfigProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AppConfig>(DEFAULT_CONFIG);

  useEffect(() => {
    let cancelled = false;
    api
      .config()
      .then((c) => {
        if (!cancelled && c.langfuse_url) setConfig(c);
      })
      .catch(() => {
        // fall back silently to default — the sidebar link still works
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <ConfigContext.Provider value={config}>{children}</ConfigContext.Provider>
  );
}

export function useConfig(): AppConfig {
  return useContext(ConfigContext);
}
