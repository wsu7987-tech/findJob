import { describe, expect, it } from "vitest";

import {
  applyBasicContentCleaning,
  applyEnhancedContentCleaning,
  canCommitPdfTaskCard,
  mapPdfParserLabel,
  resolvePdfPreviewContent
} from "./pdfDraftPresentation";

describe("pdf draft presentation helpers", () => {
  it("maps parser names to user-facing labels", () => {
    expect(mapPdfParserLabel("auto")).toBe("自动判断");
    expect(mapPdfParserLabel("pymupdf4llm_markdown")).toBe("PyMuPDF Markdown");
    expect(mapPdfParserLabel("rapid_ocr")).toBe("RapidOCR");
  });

  it("uses preview text for markdown preview mode", () => {
    expect(
      resolvePdfPreviewContent({
        activePreviewResult: {
          markdown_text: "# Title\n\nBody",
          preview_text: "# Title",
          raw_text: "Title Body"
        },
        viewMode: "preview",
        cleaningMode: "basic"
      })
    ).toEqual({
      mode: "markdown",
      content: "# Title"
    });
  });

  it("uses full markdown text for full mode", () => {
    expect(
      resolvePdfPreviewContent({
        activePreviewResult: {
          markdown_text: "# Title\n\nBody",
          preview_text: "# Title",
          raw_text: "Title Body"
        },
        viewMode: "full",
        cleaningMode: "basic"
      })
    ).toEqual({
      mode: "markdown",
      content: "# Title\n\nBody"
    });
  });

  it("uses raw text for full text mode when markdown is unavailable", () => {
    expect(
      resolvePdfPreviewContent({
        activePreviewResult: {
          markdown_text: null,
          preview_text: "Alpha...",
          raw_text: "Alpha Beta Gamma"
        },
        viewMode: "full",
        cleaningMode: "basic"
      })
    ).toEqual({
      mode: "text",
      content: "Alpha Beta Gamma"
    });
  });

  it("applies basic cleaning before rendering preview content", () => {
    expect(
      resolvePdfPreviewContent({
        activePreviewResult: {
          markdown_text: null,
          preview_text: "Page 1\n\nBody text",
          raw_text: "Page 1\n\nBody text"
        },
        viewMode: "preview",
        cleaningMode: "basic"
      })
    ).toEqual({
      mode: "text",
      content: "Body text"
    });
  });

  it("moves obvious template noise into basic cleaning", () => {
    const rawContent = "Page 1\n导航 | 返回\nCopyright 2026 Example\n\nBody text";

    expect(applyBasicContentCleaning(rawContent)).toBe("Body text");
    expect(applyEnhancedContentCleaning(rawContent)).toBe("Body text");
  });

  it("keeps OCR-oriented repairs in enhanced cleaning only", () => {
    const rawContent =
      "Page 1\n导航 | 返回\nhttps://example.com\n\n第一段上半句\n下半句继续说明。\n\n重复行\n重复行\n\nCopyright 2026 Example";

    expect(applyBasicContentCleaning(rawContent)).toBe(
      "第一段上半句\n下半句继续说明。\n\n重复行\n重复行"
    );
    expect(applyEnhancedContentCleaning(rawContent)).toBe(
      "第一段上半句下半句继续说明。\n\n重复行"
    );
  });

  it("allows direct commit only for ready task cards", () => {
    expect(canCommitPdfTaskCard("ready")).toBe(true);
    expect(canCommitPdfTaskCard("running")).toBe(false);
    expect(canCommitPdfTaskCard("failed")).toBe(false);
  });
});
