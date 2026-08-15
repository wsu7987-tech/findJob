const dateTimeFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
  hourCycle: "h23"
});

const readPart = (parts: Intl.DateTimeFormatPart[], type: Intl.DateTimeFormatPartTypes) =>
  parts.find((part) => part.type === type)?.value ?? "";

export const formatDateTime = (value?: string | null) => {
  if (!value) {
    return "未返回";
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  const parts = dateTimeFormatter.formatToParts(parsed);
  const year = readPart(parts, "year");
  const month = readPart(parts, "month");
  const day = readPart(parts, "day");
  const hour = readPart(parts, "hour");
  const minute = readPart(parts, "minute");
  const second = readPart(parts, "second");

  if (!year || !month || !day || !hour || !minute || !second) {
    return dateTimeFormatter.format(parsed);
  }

  return `${year}-${month}-${day} ${hour}:${minute}:${second}`;
};

export const formatCount = (value: number) => new Intl.NumberFormat("zh-CN").format(value);
