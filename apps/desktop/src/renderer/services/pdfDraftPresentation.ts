import type { PdfDraftParseResult } from "@/types";

import { applyBasicContentCleaning, applyEnhancedContentCleaning } from "./contentCleaning";

export const mapPdfParserLabel = (parserName: string) => {
  switch (parserName) {
    case "auto":
      return "自动判断";
    case "pymupdf4llm_markdown":
      return "PyMuPDF Markdown";
    case "rapid_ocr":
      return "RapidOCR";
    default:
      return parserName;
  }
};

export const canCommitPdfTaskCard = (status: string) => status === "ready";

export const resolvePdfPreviewContent = ({
  activePreviewResult,
  viewMode,
  cleaningMode = "basic"
}: {
  activePreviewResult: Pick<PdfDraftParseResult, "markdown_text" | "preview_text" | "raw_text"> | null;
  viewMode: "preview" | "full";
  cleaningMode?: "basic" | "enhanced";
}) => {
  const cleanContent =
    cleaningMode === "enhanced" ? applyEnhancedContentCleaning : applyBasicContentCleaning;
  const markdownText = activePreviewResult?.markdown_text?.trim();

  if (viewMode === "full") {
    if (markdownText) {
      return {
        mode: "markdown" as const,
        content: cleanContent(markdownText)
      };
    }

    return {
      mode: "text" as const,
      content: cleanContent(
        activePreviewResult?.raw_text || activePreviewResult?.preview_text || "还没有可显示的解析结果。"
      )
    };
  }

  if (markdownText) {
    return {
      mode: "markdown" as const,
      content: cleanContent(activePreviewResult?.preview_text?.trim() || markdownText)
    };
  }

  return {
    mode: "text" as const,
    content: cleanContent(
      activePreviewResult?.preview_text || activePreviewResult?.raw_text || "还没有可显示的解析结果。"
    )
  };
};

export { applyBasicContentCleaning, applyEnhancedContentCleaning };
