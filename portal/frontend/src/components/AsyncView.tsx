import { useEffect, useState } from "react";
import { Alert, Text } from "@clickhouse/click-ui";

type LoadState<T> =
  | { kind: "loading" }
  | { kind: "error"; error: string }
  | { kind: "ready"; data: T };

export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): LoadState<T> {
  const [state, setState] = useState<LoadState<T>>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ kind: "loading" });
    load()
      .then((data) => {
        if (!cancelled) setState({ kind: "ready", data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            kind: "error",
            error: err instanceof Error ? err.message : String(err),
          });
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}

export function AsyncView<T>({
  state,
  children,
}: {
  state: LoadState<T>;
  children: (data: T) => React.ReactNode;
}) {
  if (state.kind === "loading") {
    return (
      <div className="empty">
        <Text color="muted">Loading…</Text>
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <Alert
        state="danger"
        title="Failed to load"
        text={state.error}
        showIcon
      />
    );
  }
  return <>{children(state.data)}</>;
}
