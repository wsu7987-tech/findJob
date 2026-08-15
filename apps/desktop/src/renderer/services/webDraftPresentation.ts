import type { WebDraftParseResult } from "@/types";

import { applyBasicContentCleaning, applyEnhancedContentCleaning } from "./contentCleaning";

export const mapWebParserLabel = (parserName: string) => {
  switch (parserName) {
    case "playwright_dom":
      return "Playwright DOM";
    default:
      return parserName;
  }
};

export const canCommitWebTaskCard = (status: string) => status === "ready";

export const resolveWebPreviewContent = ({
  activePreviewResult,
  viewMode,
  cleaningMode = "basic"
}: {
  activePreviewResult: Pick<WebDraftParseResult, "markdown_text" | "preview_text" | "raw_text"> | null;
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
        activePreviewResult?.raw_text || activePreviewResult?.preview_text || "还没有可显示的抓取结果。"
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
      activePreviewResult?.preview_text || activePreviewResult?.raw_text || "还没有可显示的抓取结果。"
    )
  };
};
