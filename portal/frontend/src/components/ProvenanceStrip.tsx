import { useConfig } from "../lib/config";

interface Props {
  /** Optional deep link into Langfuse for the current page */
  deepLink?: string;
}

export default function ProvenanceStrip({ deepLink }: Props) {
  const { langfuse_url } = useConfig();
  const href = deepLink ?? langfuse_url;
  return (
    <div className="provenance-strip">
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
        <circle cx="8" cy="8" r="3" fill="currentColor" opacity="0.4" />
        <circle cx="8" cy="8" r="1.5" fill="currentColor" />
      </svg>
      <span>
        Data synced from{" "}
        <a href={href} target="_blank" rel="noopener noreferrer">
          Langfuse ↗
        </a>
      </span>
    </div>
  );
}
