import { describe, expect, it } from "vitest";

import { resolveWebPreviewContent } from "./webDraftPresentation";
import { applyBasicContentCleaning, applyEnhancedContentCleaning } from "./contentCleaning";

describe("web draft presentation helpers", () => {
  it("applies basic cleaning to preview content by default", () => {
    expect(
      resolveWebPreviewContent({
        activePreviewResult: {
          markdown_text: null,
          preview_text: "Page 2\n\n正文内容",
          raw_text: "Page 2\n\n正文内容"
        },
        viewMode: "preview",
        cleaningMode: "basic"
      })
    ).toEqual({
      mode: "text",
      content: "正文内容"
    });
  });

  it("removes common web template noise in basic cleaning", () => {
    const rawContent = "https://example.com\n分享\n登录\n\n正文内容";

    expect(applyBasicContentCleaning(rawContent)).toBe("正文内容");
  });

  it("uses enhanced cleaning for OCR-style line repair", () => {
    const rawContent = "第一行残句\n第二行接上。\n\n同一行\n同一行";

    expect(applyBasicContentCleaning(rawContent)).toBe(
      "第一行残句\n第二行接上。\n\n同一行\n同一行"
    );
    expect(applyEnhancedContentCleaning(rawContent)).toBe("第一行残句第二行接上。\n\n同一行");
  });
});
