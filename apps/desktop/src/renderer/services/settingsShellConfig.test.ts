import { describe, expect, it } from "vitest";

import { buildSettingsSavePayload, hasDesktopShellConfigChanges } from "./settingsShellConfig";

describe("settingsShellConfig", () => {
  const form = {
    output_root: "D:\\KnowledgeCurator\\outputs",
    summary_output_dir: "D:\\KnowledgeCurator\\outputs\\summary",
    report_output_dir: "D:\\KnowledgeCurator\\outputs\\reports",
    llm_provider: "openai-compatible",
    llm_model: "gpt-4.1-mini",
    llm_base_url: "https://example.com/v1",
    llm_api_key: "llm-secret",
    embedding_provider: "openai",
    embedding_model: "text-embedding-3-large",
    embedding_base_url: "https://example.com/embed",
    embedding_api_key: "embedding-secret",
    fetch_concurrency: 2,
    llm_concurrency: 3,
    embedding_concurrency: 4,
    quick_capture_hotkey: "CommandOrControl+Alt+Q",
    quick_capture_screenshot_hotkey: "CommandOrControl+Alt+S",
    close_to_tray: false,
    quick_capture_always_on_top: false
  };

  it("includes desktop shell fields in the main save payload", () => {
    expect(
      buildSettingsSavePayload(form, {
        llmApiKeyTouched: false,
        embeddingApiKeyTouched: false
      })
    ).toMatchObject({
      quick_capture_hotkey: "CommandOrControl+Alt+Q",
      quick_capture_screenshot_hotkey: "CommandOrControl+Alt+S",
      close_to_tray: false,
      quick_capture_always_on_top: false
    });
  });

  it("detects when shell config should be refreshed after save", () => {
    expect(
      hasDesktopShellConfigChanges(form, {
        quick_capture_hotkey: "CommandOrControl+Shift+Space",
        quick_capture_screenshot_hotkey: "CommandOrControl+Shift+4",
        close_to_tray: true,
        quick_capture_always_on_top: true
      })
    ).toBe(true);

    expect(
      hasDesktopShellConfigChanges(form, {
        quick_capture_hotkey: "CommandOrControl+Alt+Q",
        quick_capture_screenshot_hotkey: "CommandOrControl+Alt+S",
        close_to_tray: false,
        quick_capture_always_on_top: false
      })
    ).toBe(false);
  });
});
