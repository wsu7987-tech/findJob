import { describe, expect, it } from "vitest";

import { formatDateTime } from "./format";

describe("formatDateTime", () => {
  it("formats ISO timestamps into yyyy-MM-dd HH:mm:ss", () => {
    expect(formatDateTime("2026-04-17T20:24:28Z")).toMatch(
      /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/
    );
  });

  it("preserves unparseable values", () => {
    expect(formatDateTime("not-a-date")).toBe("not-a-date");
  });

  it("returns a fallback label for empty input", () => {
    expect(formatDateTime(null)).toBe("未返回");
  });
});
