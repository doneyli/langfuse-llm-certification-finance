export const DATASETS = [
  {
    slug: "certification/financebench-sample",
    label: "FinanceBench · sample",
    kind: "numerical" as const,
  },
  {
    slug: "certification/fpb-sample",
    label: "Financial PhraseBank · sample",
    kind: "sentiment" as const,
  },
  {
    slug: "certification/financebench-v1",
    label: "FinanceBench · v1",
    kind: "numerical" as const,
  },
  {
    slug: "certification/fpb-v1",
    label: "Financial PhraseBank · v1",
    kind: "sentiment" as const,
  },
];

export function datasetLabel(slug: string): string {
  return DATASETS.find((d) => d.slug === slug)?.label ?? slug.split("/").pop() ?? slug;
}
