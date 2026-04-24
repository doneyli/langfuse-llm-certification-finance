import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { ClickUIProvider } from "@clickhouse/click-ui";

import "./styles.css";

import App from "./App";
import { ConfigProvider } from "./lib/config";
import { AppThemeProvider, useAppTheme } from "./lib/theme";

function ThemedRoot() {
  const { theme } = useAppTheme();
  return (
    <ClickUIProvider theme={theme}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </ClickUIProvider>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppThemeProvider>
      <ConfigProvider>
        <ThemedRoot />
      </ConfigProvider>
    </AppThemeProvider>
  </React.StrictMode>
);
