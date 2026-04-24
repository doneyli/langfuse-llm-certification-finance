import { Badge } from "@clickhouse/click-ui";
import type { CertStatus } from "../types";
import { statusToBadge } from "../lib/format";

export default function StatusBadge({
  status,
  size = "sm",
}: {
  status: CertStatus;
  size?: "sm" | "md";
}) {
  const { state, text } = statusToBadge(status);
  return <Badge state={state} size={size} text={text} />;
}
