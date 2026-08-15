export interface NamedValue {
  name: string;
  value: number;
}

const recordToSeries = (value: unknown) => {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return [] as NamedValue[];
  }

  return Object.entries(value as Record<string, unknown>)
    .map(([name, rawValue]) => ({
      name,
      value: typeof rawValue === "number" ? rawValue : Number(rawValue ?? 0)
    }))
    .filter((entry) => Number.isFinite(entry.value) && entry.value > 0);
};

export const extractReportSeries = (payload: unknown) => {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    return {
      categories: [] as NamedValue[],
      sources: [] as NamedValue[],
      trends: [] as NamedValue[]
    };
  }

  const source = payload as Record<string, unknown>;

  return {
    categories: recordToSeries(source.category_stats ?? source.categories ?? source.categoryStats),
    sources: recordToSeries(source.source_distribution ?? source.sources ?? source.sourceStats),
    trends: recordToSeries(source.reading_trend ?? source.trends ?? source.readingTrend)
  };
};
