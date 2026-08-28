import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { api } from "@/services/api";
import { getCodexBridge } from "@/services/desktop-bridge";
import type {
  FineJobCodexPendingWork,
  FineJobCodexPermissions,
  FineJobCodexSessionStatus
} from "@/types";

const emptyPending = (): FineJobCodexPendingWork => ({
  greetings: [],
  chat_replies: [],
  automation_actions: [],
  chat_actions: []
});

export const useFineJobCodexStore = defineStore("fine-job-codex", () => {
  const status = ref<FineJobCodexSessionStatus>("idle");
  const runId = ref<string | null>(null);
  const statusMessage = ref("");
  const permissions = ref<FineJobCodexPermissions | null>(null);
  const pending = ref<FineJobCodexPendingWork>(emptyPending());
  const loading = ref(false);
  const error = ref<string | null>(null);
  let removeStatusListener: (() => void) | null = null;

  const pendingCount = computed(
    () =>
      pending.value.greetings.length +
      pending.value.chat_replies.length +
      pending.value.automation_actions.length +
      pending.value.chat_actions.length
  );

  const connectStatus = () => {
    if (removeStatusListener) return;
    const bridge = getCodexBridge();
    removeStatusListener = bridge?.onCodexStatus?.((payload) => {
      status.value = payload.status as FineJobCodexSessionStatus;
      runId.value = payload.runId;
      statusMessage.value = payload.message;
    }) ?? null;
  };

  const load = async () => {
    loading.value = true;
    error.value = null;
    connectStatus();
    try {
      [permissions.value, pending.value] = await Promise.all([
        api.getFineJobCodexPermissions(),
        api.getFineJobCodexPendingWork()
      ]);
      const state = await getCodexBridge()?.getCodexState?.();
      if (state) {
        status.value = state.status as FineJobCodexSessionStatus;
        runId.value = state.runId;
      }
    } catch (value) {
      error.value = (value as Error).message;
    } finally {
      loading.value = false;
    }
  };

  const start = async (cols: number, rows: number, resume = false) => {
    const bridge = getCodexBridge();
    if (!bridge) throw new Error("Codex 终端只在 FineJob 桌面端可用。");
    const state = resume
      ? await bridge.resumeCodex?.({ cols, rows })
      : await bridge.startCodex?.({ cols, rows });
    if (state) {
      status.value = state.status as FineJobCodexSessionStatus;
      runId.value = state.runId;
    }
  };

  const savePermissions = async (next: FineJobCodexPermissions) => {
    permissions.value = await api.updateFineJobCodexPermissions({
      enabled: next.enabled,
      permissions: next.permissions
    });
  };

  const decide = async (
    resourceType: "greeting_preview" | "chat_reply",
    item: { id: string; version?: number; text_version?: number; final_message?: string; final_text?: string },
    operation: "approve" | "reject"
  ) => {
    await api.decideFineJobCodexPending(resourceType, item.id, operation, {
      expected_version: item.version ?? item.text_version ?? 1,
      final_text: item.final_message ?? item.final_text ?? "",
      note: operation === "reject" ? "用户在 Codex 工作台拒绝" : ""
    });
    pending.value = await api.getFineJobCodexPendingWork();
  };

  return {
    status,
    runId,
    statusMessage,
    permissions,
    pending,
    pendingCount,
    loading,
    error,
    load,
    start,
    savePermissions,
    decide
  };
});
