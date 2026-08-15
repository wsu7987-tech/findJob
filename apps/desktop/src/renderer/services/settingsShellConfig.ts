import type { AppConfigPayload } from "../types";

interface SettingsSaveForm {
  output_root: string;
  summary_output_dir: string;
  report_output_dir: string;
  llm_provider: string;
  llm_model: string;
  llm_base_url: string;
  llm_api_key: string;
  embedding_provider: string;
  embedding_model: string;
  embedding_base_url: string;
  embedding_api_key: string;
  fetch_concurrency: number;
  llm_concurrency: number;
  embedding_concurrency: number;
  quick_capture_hotkey: string;
  quick_capture_screenshot_hotkey: string;
  close_to_tray: boolean;
  quick_capture_always_on_top: boolean;
}

interface SettingsSaveOptions {
  llmApiKeyTouched: boolean;
  embeddingApiKeyTouched: boolean;
}

const normalizeNullableString = (value: string) => value || null;

export const buildSettingsSavePayload = (
  form: SettingsSaveForm,
  options: SettingsSaveOptions
): Partial<AppConfigPayload> => {
  const payload: Partial<AppConfigPayload> = {
    output_root: form.output_root,
    summary_output_dir: form.summary_output_dir,
    report_output_dir: form.report_output_dir,
    llm_provider: normalizeNullableString(form.llm_provider),
    llm_model: normalizeNullableString(form.llm_model),
    llm_base_url: normalizeNullableString(form.llm_base_url),
    embedding_provider: normalizeNullableString(form.embedding_provider),
    embedding_model: normalizeNullableString(form.embedding_model),
    embedding_base_url: normalizeNullableString(form.embedding_base_url),
    fetch_concurrency: form.fetch_concurrency,
    llm_concurrency: form.llm_concurrency,
    embedding_concurrency: form.embedding_concurrency,
    quick_capture_hotkey: normalizeNullableString(form.quick_capture_hotkey),
    quick_capture_screenshot_hotkey: normalizeNullableString(form.quick_capture_screenshot_hotkey),
    close_to_tray: form.close_to_tray,
    quick_capture_always_on_top: form.quick_capture_always_on_top
  };

  if (options.llmApiKeyTouched) {
    payload.llm_api_key = normalizeNullableString(form.llm_api_key.trim());
  }
  if (options.embeddingApiKeyTouched) {
    payload.embedding_api_key = normalizeNullableString(form.embedding_api_key.trim());
  }

  return payload;
};

export const hasDesktopShellConfigChanges = (
  form: Pick<
    SettingsSaveForm,
    | "quick_capture_hotkey"
    | "quick_capture_screenshot_hotkey"
    | "close_to_tray"
    | "quick_capture_always_on_top"
  >,
  config: Partial<AppConfigPayload> | null | undefined
) =>
  (config?.quick_capture_hotkey ?? "") !== form.quick_capture_hotkey ||
  (config?.quick_capture_screenshot_hotkey ?? "") !== form.quick_capture_screenshot_hotkey ||
  (config?.close_to_tray ?? true) !== form.close_to_tray ||
  (config?.quick_capture_always_on_top ?? true) !== form.quick_capture_always_on_top;
