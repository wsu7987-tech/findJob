import type { FineJobAnalyticsPreset } from "@/types";

export const ANALYTICS_TIME_ZONE = "Asia/Shanghai";

const pad = (value: number) => String(value).padStart(2, "0");

const formatDate = (value: Date) =>
  `${value.getUTCFullYear()}-${pad(value.getUTCMonth() + 1)}-${pad(value.getUTCDate())}`;

const shanghaiDate = (now: Date) => {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: ANALYTICS_TIME_ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit"
  }).formatToParts(now);
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return new Date(Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day)));
};

const shiftDays = (value: Date, days: number) => {
  const result = new Date(value);
  result.setUTCDate(result.getUTCDate() + days);
  return result;
};

export const getAnalyticsPresetRange = (
  preset: Exclude<FineJobAnalyticsPreset, "custom">,
  now = new Date()
) => {
  const today = shanghaiDate(now);
  if (preset === "today") return { from: formatDate(today), to: formatDate(today) };
  if (preset === "last7") {
    return { from: formatDate(shiftDays(today, -6)), to: formatDate(today) };
  }
  if (preset === "last30") {
    return { from: formatDate(shiftDays(today, -29)), to: formatDate(today) };
  }
  if (preset === "thisMonth") {
    const firstDay = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1));
    return { from: formatDate(firstDay), to: formatDate(today) };
  }

  // 以 Asia/Shanghai 的自然日计算周一到今天，避免使用浏览器本地时区。
  const daysFromMonday = (today.getUTCDay() + 6) % 7;
  return { from: formatDate(shiftDays(today, -daysFromMonday)), to: formatDate(today) };
};

export const formatAnalyticsRate = (value: number | null | undefined) => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
};

export const formatAnalyticsRateWithDenominator = (
  value: number | null | undefined,
  denominator: number | null | undefined
) => {
  if (denominator === null || denominator === undefined || denominator <= 0) return "—";
  return formatAnalyticsRate(value);
};

export const displayAnalyticsMetric = (value: number | null | undefined) => {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value;
};
