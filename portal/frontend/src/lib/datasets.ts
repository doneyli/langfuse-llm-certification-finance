// Dataset names are discovered at runtime from GET /api/datasets (see
// api.datasets() and AppShell) — there is no hardcoded dataset list anymore.
// This module only maps slugs to display labels and sidebar icons.

/** Friendly names for well-known datasets. */
const KNOWN_LABELS: Record<string, string> = {
  "certification/financebench-sample": "FinanceBench · sample",
  "certification/fpb-sample": "Financial PhraseBank · sample",
  "certification/financebench-v1": "FinanceBench · v1",
  "certification/fpb-v1": "Financial PhraseBank · v1",
};

/** Trailing variant hints we surface as a "· suffix" (e.g. -sample, -v1). */
const SUFFIX_HINT = /-(sample|v\d+)$/;

/**
 * Human-readable label for a dataset slug. Known slugs get their curated
 * friendly name; unknown ones are derived: strip the "certification/" prefix,
 * split off a -sample/-vN suffix hint, hyphens → spaces, title-case.
 * e.g. "certification/advisory-adversarial" → "Advisory Adversarial",
 *      "certification/promoted-traces-v2" → "Promoted Traces · v2".
 */
export function datasetLabel(slug: string): string {
  const known = KNOWN_LABELS[slug];
  if (known) return known;

  let base = slug.split("/").pop() ?? slug;
  let hint = "";
  const m = base.match(SUFFIX_HINT);
  if (m) {
    hint = ` · ${m[1]}`;
    base = base.slice(0, -m[0].length);
  }
  const words = base
    .split("-")
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
  return `${words || base}${hint}`;
}

/** Sidebar icon: chat for sentiment-style datasets, bar-chart otherwise. */
export function datasetIcon(slug: string): "bar-chart" | "chat" {
  return /fpb|sentiment|phrasebank/i.test(slug) ? "chat" : "bar-chart";
}
