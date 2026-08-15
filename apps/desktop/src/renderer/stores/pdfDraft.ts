import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { saveTextFile } from "@/services/desktop-bridge";
import { NetworkError, api } from "@/services/api";
import type {
  PdfDraft,
  PoolCommitMetadataRequest,
  PdfDraftCreateRequest,
  PdfDraftParseResult,
  PdfDraftParserName,
  PdfDraftPreviewPage,
  PdfReparseJob
} from "@/types";

const clampPageNumber = (pageNumber: number, pageCount: number) => {
  if (pageCount <= 0) {
    return Math.max(1, pageNumber);
  }
  return Math.min(Math.max(1, pageNumber), pageCount);
};

export const usePdfDraftStore = defineStore("pdfDraft", () => {
  const draftsById = ref<Record<string, PdfDraft>>({});
  const activeDraftId = ref<string | null>(null);
  const drawerOpen = ref(false);
  const creating = ref(false);
  const reparsing = ref(false);
  const saving = ref(false);
  const committing = ref(false);
  const loadingPreviewPage = ref(false);
  const error = ref<string | null>(null);
  const connectionUnavailable = ref(false);
  const selectedParser = ref<PdfDraftParserName>("auto");
  const jobs = ref<PdfReparseJob[]>([]);
  const activeJobId = ref<string | null>(null);
  const selectedParseResultId = ref<string | null>(null);
  const currentPageNumber = ref(1);
  const previewPageCache = ref<Record<string, PdfDraftPreviewPage>>({});
  let reparseAbortController: AbortController | null = null;
  let pollTimer: ReturnType<typeof setTimeout> | null = null;

  const draft = computed(() =>
    activeDraftId.value ? draftsById.value[activeDraftId.value] ?? null : null
  );
  const draftList = computed(() => Object.values(draftsById.value));
  const activeJobs = computed(() =>
    jobs.value.filter((item) => item.status === "queued" || item.status === "running")
  );

  const savedParseResult = computed(() =>
    draft.value?.parse_results.find((item) => item.id === draft.value?.saved_parse_result_id) ?? null
  );

  const latestPreviewResult = computed(() =>
    draft.value?.parse_results.find((item) => item.id === draft.value?.latest_preview_result_id) ?? null
  );

  const activePreviewResult = computed(() => {
    if (!draft.value) {
      return null;
    }
    if (selectedParseResultId.value) {
      const selected = draft.value.parse_results.find((item) => item.id === selectedParseResultId.value);
      if (selected) {
        return selected;
      }
    }
    return latestPreviewResult.value ?? savedParseResult.value;
  });

  const activeJob = computed(
    () =>
      activeJobs.value.find((item) => item.id === activeJobId.value) ??
      activeJobs.value.find((item) => item.draft_id === activeDraftId.value) ??
      null
  );

  const activeTaskCount = computed(() => activeJobs.value.length);

  const activePreviewPage = computed(() => {
    if (!draft.value || !activePreviewResult.value) {
      return null;
    }
    return (
      previewPageCache.value[`${draft.value.id}:${activePreviewResult.value.id}:${currentPageNumber.value}`] ??
      null
    );
  });

  const hasUnsavedPreview = computed(
    () =>
      Boolean(draft.value?.latest_preview_result_id) &&
      draft.value?.latest_preview_result_id !== draft.value?.saved_parse_result_id &&
      latestPreviewResult.value?.status === "preview"
  );

  const taskCards = computed(() =>
    draftList.value
      .map((item) => {
        const currentJob = activeJobs.value.find((job) => job.draft_id === item.id) ?? null;
        const latestResult =
          item.parse_results.find((result) => result.id === item.latest_preview_result_id) ?? null;
        let status = "completed";
        if (currentJob?.status === "queued" || currentJob?.status === "running") {
          status = currentJob.status;
        } else if (item.saved_parse_result_id) {
          status = "ready";
        } else if (latestResult?.status === "failed") {
          status = "failed";
        }
        return {
          draft: item,
          job: currentJob,
          latestResult,
          status,
          parserName: currentJob?.parser_name ?? latestResult?.parser_name ?? "auto",
          progressLabel: currentJob
            ? currentJob.total_pages > 0
              ? `${currentJob.processed_pages} / ${currentJob.total_pages} 页`
              : `${currentJob.processed_pages} / ? 页`
            : item.saved_parse_result_id
              ? "已保存，可加入总结池"
              : "预览已完成，待保存",
          updatedAt: item.updated_at
        };
      })
      .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt))
  );

  const applyDraft = (
    nextDraft: PdfDraft,
    options?: { activate?: boolean; openDrawer?: boolean }
  ) => {
    draftsById.value = {
      ...draftsById.value,
      [nextDraft.id]: nextDraft
    };
    if (options?.activate !== false) {
      activeDraftId.value = nextDraft.id;
    }
    if (
      !selectedParseResultId.value ||
      !nextDraft.parse_results.some((item) => item.id === selectedParseResultId.value)
    ) {
      selectedParseResultId.value =
        nextDraft.latest_preview_result_id ?? nextDraft.saved_parse_result_id ?? null;
    }
    if (options?.openDrawer !== false) {
      drawerOpen.value = true;
    }
    connectionUnavailable.value = false;
  };

  const clearState = () => {
    if (pollTimer) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    draftsById.value = {};
    activeDraftId.value = null;
    drawerOpen.value = false;
    selectedParser.value = "auto";
    selectedParseResultId.value = null;
    reparsing.value = false;
    saving.value = false;
    committing.value = false;
    jobs.value = [];
    activeJobId.value = null;
    currentPageNumber.value = 1;
    previewPageCache.value = {};
    reparseAbortController = null;
  };

  const closeDrawer = () => {
    drawerOpen.value = false;
  };

  const reopenDrawer = () => {
    if (!draft.value) {
      return;
    }
    drawerOpen.value = true;
  };

  const removeDraftState = (draftId: string) => {
    const nextDrafts = { ...draftsById.value };
    delete nextDrafts[draftId];
    draftsById.value = nextDrafts;
    jobs.value = jobs.value.filter((item) => item.draft_id !== draftId);
    if (activeDraftId.value === draftId) {
      activeDraftId.value = Object.keys(nextDrafts)[0] ?? null;
      selectedParseResultId.value = activeDraftId.value
        ? nextDrafts[activeDraftId.value]?.latest_preview_result_id ??
          nextDrafts[activeDraftId.value]?.saved_parse_result_id ??
          null
        : null;
      drawerOpen.value = false;
    }
  };

  const buildExportPayload = () => {
    if (!activePreviewResult.value || !draft.value) {
      return null;
    }
    const result = activePreviewResult.value;
    const content = result.markdown_text?.trim() ? result.markdown_text : result.raw_text;
    if (!content.trim()) {
      return null;
    }
    const extension = result.markdown_text?.trim() ? "md" : "txt";
    const baseName = draft.value.source_name.replace(/\.[^.]+$/, "");
    return {
      defaultPath: `${baseName}-${result.parser_name}.${extension}`,
      content,
      filters: [
        {
          name: extension === "md" ? "Markdown 文件" : "文本文件",
          extensions: [extension]
        }
      ]
    };
  };

  const cancelReparse = async () => {
    if (!draft.value || !activeJob.value) {
      return false;
    }
    reparseAbortController?.abort();
    reparseAbortController = null;
    reparsing.value = false;
    try {
      const response = await api.cancelPdfReparseJob(draft.value.id, activeJob.value.id);
      if (draft.value.saved_parse_result_id) {
        jobs.value = jobs.value.filter((item) => item.id !== response.job.id);
        const refreshed = await api.getPdfDraft(draft.value.id);
        applyDraft(refreshed.draft);
        selectedParseResultId.value = refreshed.draft.saved_parse_result_id ?? null;
        activeJobId.value = null;
      } else {
        await api.deletePdfDraft(draft.value.id);
        removeDraftState(draft.value.id);
      }
      return true;
    } catch {
      return false;
    }
  };

  const createDraft = async (payload: PdfDraftCreateRequest) => {
    creating.value = true;
    error.value = null;
    try {
      const response = await api.createPdfDraft(payload);
      applyDraft(response.draft);
      jobs.value = [response.job, ...jobs.value.filter((item) => item.id !== response.job.id)];
      activeJobId.value = response.job.id;
      selectedParseResultId.value = response.draft.latest_preview_result_id ?? null;
      currentPageNumber.value = 1;
      startPolling();
      return response.draft;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      creating.value = false;
    }
  };

  const reparseDraft = async () => {
    if (!draft.value) {
      return null;
    }
    if (reparsing.value) {
      await cancelReparse();
    }
    const controller = new AbortController();
    reparseAbortController = controller;
    reparsing.value = true;
    error.value = null;
    try {
      const response = await api.reparsePdfDraft(
        draft.value.id,
        {
          parser_name: selectedParser.value
        },
        controller.signal
      );
      applyDraft(response.draft);
      jobs.value = [response.job, ...jobs.value.filter((item) => item.id !== response.job.id)];
      activeJobId.value = response.job.id;
      selectedParseResultId.value = response.draft.latest_preview_result_id ?? null;
      currentPageNumber.value = 1;
      startPolling();
      return response.draft;
    } catch (errorValue) {
      if (errorValue instanceof DOMException && errorValue.name === "AbortError") {
        return null;
      }
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      if (reparseAbortController === controller) {
        reparseAbortController = null;
      }
      reparsing.value = false;
    }
  };

  const cacheKey = (draftId: string, parseResultId: string, pageNumber: number) =>
    `${draftId}:${parseResultId}:${pageNumber}`;

  const loadPreviewPage = async (pageNumber: number) => {
    if (!draft.value || !activePreviewResult.value) {
      return null;
    }
    const resolvedPageNumber = clampPageNumber(pageNumber, activePreviewResult.value.page_count);
    currentPageNumber.value = resolvedPageNumber;
    const key = cacheKey(draft.value.id, activePreviewResult.value.id, resolvedPageNumber);
    const cached = previewPageCache.value[key];
    if (cached) {
      return cached;
    }
    loadingPreviewPage.value = true;
    try {
      const response = await api.getPdfDraftPreviewPage(
        draft.value.id,
        activePreviewResult.value.id,
        resolvedPageNumber
      );
      currentPageNumber.value = response.page.page_number;
      previewPageCache.value = {
        ...previewPageCache.value,
        [cacheKey(draft.value.id, activePreviewResult.value.id, response.page.page_number)]: response.page
      };
      return response.page;
    } finally {
      loadingPreviewPage.value = false;
    }
  };

  const refreshJobs = async () => {
    const response = await api.listPdfReparseJobs();
    jobs.value = response.jobs;
    if (response.jobs.length === 0) {
      activeJobId.value = null;
    } else if (!activeJobId.value || !response.jobs.some((item) => item.id === activeJobId.value)) {
      activeJobId.value = response.jobs[0]?.id ?? null;
    }
    for (const job of response.jobs) {
      if (!draftsById.value[job.draft_id]) {
        try {
          const draftResponse = await api.getPdfDraft(job.draft_id);
          applyDraft(draftResponse.draft, { activate: false, openDrawer: false });
        } catch {
          // ignore stale draft fetch failures during refresh
        }
      }
    }
    return response.jobs;
  };

  const startPolling = () => {
    if (pollTimer) {
      clearTimeout(pollTimer);
    }
    pollTimer = setTimeout(async () => {
      try {
        const response = await refreshJobs();
        const activeDraftIds = new Set(
          response
            .filter((item) => item.status === "completed" || item.status === "failed" || item.status === "cancelled")
            .map((item) => item.draft_id)
        );
        for (const draftId of activeDraftIds) {
          try {
            const refreshed = await api.getPdfDraft(draftId);
            applyDraft(refreshed.draft, {
              activate: activeDraftId.value === draftId,
              openDrawer: activeDraftId.value === draftId && drawerOpen.value
            });
          } catch {
            // ignore missing drafts during poll refresh
          }
        }
        if (!response.some((item) => item.status === "queued" || item.status === "running")) {
          pollTimer = null;
          return;
        }
        startPolling();
      } catch {
        pollTimer = null;
      }
    }, 1000);
  };

  const saveCurrentResult = async () => {
    if (!draft.value || !activePreviewResult.value) {
      return null;
    }
    saving.value = true;
    error.value = null;
    try {
      const response = await api.savePdfDraftParseResult(draft.value.id, activePreviewResult.value.id);
      applyDraft(response.draft);
      selectedParseResultId.value = response.draft.saved_parse_result_id ?? null;
      return response.draft;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      saving.value = false;
    }
  };

  const commitDraft = async (payload?: PoolCommitMetadataRequest) => {
    if (!draft.value) {
      return null;
    }
    committing.value = true;
    error.value = null;
    try {
      const response = await api.commitPdfDraft(draft.value.id, payload);
      removeDraftState(draft.value.id);
      connectionUnavailable.value = false;
      return response.item;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      committing.value = false;
    }
  };

  const discardDraft = async () => {
    if (!draft.value) {
      drawerOpen.value = false;
      return;
    }
    const draftId = draft.value.id;
    error.value = null;
    try {
      if (reparsing.value) {
        await cancelReparse();
      }
      await api.deletePdfDraft(draftId);
      connectionUnavailable.value = false;
    } catch (errorValue) {
      connectionUnavailable.value = errorValue instanceof NetworkError;
      error.value = (errorValue as Error).message;
      throw errorValue;
    } finally {
      removeDraftState(draftId);
    }
  };

  const openTask = async (draftId: string, jobId?: string) => {
    activeDraftId.value = draftId;
    activeJobId.value = jobId ?? activeJobs.value.find((item) => item.draft_id === draftId)?.id ?? null;
    currentPageNumber.value = 1;
    drawerOpen.value = true;
    if (!draftsById.value[draftId]) {
      const response = await api.getPdfDraft(draftId);
      applyDraft(response.draft, { activate: true, openDrawer: true });
    }
    selectedParseResultId.value =
      draftsById.value[draftId]?.latest_preview_result_id ??
      draftsById.value[draftId]?.saved_parse_result_id ??
      null;
  };

  const selectPreviewResult = (parseResultId: string): PdfDraftParseResult | null => {
    const result = draft.value?.parse_results.find((item) => item.id === parseResultId) ?? null;
    if (result) {
      selectedParseResultId.value = parseResultId;
      currentPageNumber.value = 1;
    }
    return result;
  };

  const saveActiveResultToLocal = async () => {
    const payload = buildExportPayload();
    if (!payload) {
      return null;
    }
    return saveTextFile({
      title: "保存解析结果",
      defaultPath: payload.defaultPath,
      content: payload.content,
      filters: payload.filters
    });
  };

  return {
    draft,
    draftList,
    drawerOpen,
    creating,
    reparsing,
    saving,
    committing,
    loadingPreviewPage,
    error,
    connectionUnavailable,
    selectedParser,
    jobs,
    activeDraftId,
    activeJobId,
    currentPageNumber,
    savedParseResult,
    latestPreviewResult,
    activePreviewResult,
    activeJob,
    activeTaskCount,
    activePreviewPage,
    taskCards,
    hasUnsavedPreview,
    selectedParseResultId,
    createDraft,
    reparseDraft,
    saveCurrentResult,
    saveActiveResultToLocal,
    loadPreviewPage,
    refreshJobs,
    cancelReparse,
    commitDraft,
    closeDrawer,
    reopenDrawer,
    openTask,
    discardDraft,
    selectPreviewResult
  };
});
