import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { api } from "@/services/api";
import { applyEnhancedContentCleaning } from "@/services/contentCleaning";

const OCR_RUNNING_TEXT = "OCR \u8bc6\u522b\u4e2d…";
const OCR_FAILED_PREFIX = "OCR \u5931\u8d25\uff1a";
const OCR_READY_TEXT = "OCR \u5df2\u5c31\u7eea";
const OCR_IDLE_TEXT = "\u672a\u8fdb\u884c OCR";

export const useQuickCaptureStore = defineStore("quickCapture", () => {
  const title = ref("");
  const category = ref("");
  const tags = ref<string[]>([]);
  const body = ref("");
  const capturedAt = ref<string | null>(null);
  const captureSource = ref<"manual" | "screenshot_ocr">("manual");
  const loading = ref(false);
  const committing = ref(false);
  const suggestingMetadata = ref(false);
  const error = ref<string | null>(null);
  const warnings = ref<string[]>([]);
  const lastOcrAt = ref<string | null>(null);
  const preCleaningBody = ref<string | null>(null);

  const ocrStatusText = computed(() => {
    if (loading.value) {
      return OCR_RUNNING_TEXT;
    }
    if (error.value) {
      return `${OCR_FAILED_PREFIX}${error.value}`;
    }
    if (lastOcrAt.value) {
      return OCR_READY_TEXT;
    }
    return OCR_IDLE_TEXT;
  });

  const clear = () => {
    title.value = "";
    category.value = "";
    tags.value = [];
    body.value = "";
    capturedAt.value = null;
    captureSource.value = "manual";
    error.value = null;
    warnings.value = [];
    lastOcrAt.value = null;
    preCleaningBody.value = null;
  };

  const applyOcrResult = (payload: { raw_text: string; captured_at: string; warnings?: string[] }) => {
    body.value = body.value.trim().length > 0 ? `${body.value}\n\n${payload.raw_text}` : payload.raw_text;
    capturedAt.value = payload.captured_at;
    captureSource.value = "screenshot_ocr";
    warnings.value = payload.warnings ?? [];
    lastOcrAt.value = payload.captured_at;
    preCleaningBody.value = null;
  };

  const applyEnhancedCleaning = () => {
    if (!body.value.trim()) {
      return;
    }
    if (preCleaningBody.value == null) {
      preCleaningBody.value = body.value;
    }
    body.value = applyEnhancedContentCleaning(body.value);
  };

  const restorePreCleaningBody = () => {
    if (preCleaningBody.value == null) {
      return;
    }
    body.value = preCleaningBody.value;
    preCleaningBody.value = null;
  };

  const runOcr = async (imageBase64: string) => {
    loading.value = true;
    error.value = null;
    try {
      const result = await api.runQuickCaptureOcr(imageBase64);
      applyOcrResult(result);
      return result;
    } catch (errorValue) {
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      loading.value = false;
    }
  };

  const commit = async () => {
    committing.value = true;
    error.value = null;
    try {
      return await api.createPoolItem({
        source_type: "text",
        source_value: "quick-capture",
        title: title.value || null,
        raw_text: body.value,
        category: category.value || null,
        tags: [...tags.value],
        captured_at: capturedAt.value,
        capture_source: captureSource.value
      });
    } catch (errorValue) {
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      committing.value = false;
    }
  };

  const suggestMetadata = async () => {
    suggestingMetadata.value = true;
    try {
      const suggestion = await api.suggestPoolMetadata({
        source_type: "text",
        source_value: "quick-capture",
        title: title.value || null,
        raw_text: body.value || null
      });
      category.value = suggestion.category;
      tags.value = [...suggestion.tags];
      return suggestion;
    } catch (errorValue) {
      throw errorValue;
    } finally {
      suggestingMetadata.value = false;
    }
  };

  return {
    title,
    category,
    tags,
    body,
    capturedAt,
    captureSource,
    loading,
    committing,
    suggestingMetadata,
    error,
    warnings,
    lastOcrAt,
    preCleaningBody,
    ocrStatusText,
    clear,
    applyOcrResult,
    applyEnhancedCleaning,
    restorePreCleaningBody,
    runOcr,
    commit,
    suggestMetadata
  };
});
