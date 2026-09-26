import { defineStore } from "pinia";
import { ref } from "vue";
import { api, getBackendOrigin } from "@/services/api";
import type { FineJobSmartCapture } from "@/types";

export const createSmartCaptureRealtime = (dependencies: {
  getCurrent?: typeof api.getCurrentFineJobSmartCapture;
  getOrigin?: typeof getBackendOrigin;
  createEventSource?: (url: string) => EventSource;
} = {}) => {
  const current = ref<FineJobSmartCapture | null>(null);
  const loading = ref(false);
  const loaded = ref(false);
  const error = ref<string | null>(null);
  const connectionState = ref("idle");
  const listeners = new Set<(capture: FineJobSmartCapture | null) => void>();
  let currentSource: EventSource | null = null;
  let detailSource: EventSource | null = null;
  let detailId = "";
  let origin = "";
  let started = false;
  let generation = 0;
  let identityGeneration = 0;
  let refreshing: Promise<FineJobSmartCapture | null> | null = null;
  let recoveryTimer: ReturnType<typeof setTimeout> | null = null;
  let failures = 0;
  const terminal = (capture: FineJobSmartCapture) => ["completed", "stopped", "failed"].includes(capture.status);
  const sourceFactory = dependencies.createEventSource ?? (typeof EventSource === "undefined" ? null : (url: string) => new EventSource(url));
  const closeDetail = () => { detailSource?.close(); detailSource = null; detailId = ""; };

  const apply = (capture: FineJobSmartCapture | null, fromCurrent = false) => {
    if (!fromCurrent && capture?.smart_capture_id !== current.value?.smart_capture_id) return;
    const previous = current.value;
    if (previous?.smart_capture_id === capture?.smart_capture_id && previous && capture && capture.state_version < previous.state_version) return;
    if (previous?.smart_capture_id !== capture?.smart_capture_id) identityGeneration += 1;
    const changed = previous?.smart_capture_id !== capture?.smart_capture_id || previous?.state_version !== capture?.state_version;
    current.value = capture;
    loaded.value = true;
    ensureDetail();
    if (changed) listeners.forEach((listener) => listener(capture));
  };

  const scheduleRecovery = () => {
    if (!started || recoveryTimer) return;
    recoveryTimer = setTimeout(() => {
      recoveryTimer = null;
      void refreshCurrent().catch(() => undefined);
    }, [1000, 2000, 5000, 10000, 15000][Math.min(failures++, 4)]);
  };

  const refreshCurrent = (): Promise<FineJobSmartCapture | null> => {
    if (refreshing) return refreshing;
    const ownGeneration = generation;
    const ownIdentity = identityGeneration;
    loading.value = true;
    refreshing = (async () => {
      try {
        const result = await (dependencies.getCurrent ?? api.getCurrentFineJobSmartCapture)();
        if (ownGeneration !== generation || ownIdentity !== identityGeneration) return current.value;
        apply(result.smart_capture, true);
        error.value = null;
        failures = 0;
        connectionState.value = "synced";
        if (!sourceFactory) scheduleRecovery();
        return current.value;
      } catch (value) {
        if (ownGeneration === generation) {
          error.value = (value as Error).message;
          connectionState.value = "retrying";
          scheduleRecovery();
        }
        throw value;
      } finally {
        if (ownGeneration === generation) { loading.value = false; refreshing = null; }
      }
    })();
    return refreshing;
  };

  const attach = (source: EventSource, isCurrent: boolean, captureId = "") => {
    const ownGeneration = generation;
    const valid = () => started && ownGeneration === generation && (isCurrent ? currentSource === source : detailSource === source && current.value?.smart_capture_id === captureId);
    source.onmessage = (event) => {
      if (!valid()) return;
      try {
        const capture = JSON.parse(event.data) as FineJobSmartCapture | null;
        if (capture && (!capture.smart_capture_id || typeof capture.state_version !== "number")) throw new Error("采集快照格式无效。");
        // current 事件使更早发出的身份查询失效，detail 只能更新已确认的 owner。
        if (isCurrent) identityGeneration += 1;
        apply(capture, isCurrent);
        error.value = null;
        connectionState.value = "synced";
      } catch (value) {
        error.value = (value as Error).message;
        void refreshCurrent().catch(() => undefined);
      }
    };
    source.onerror = () => { if (valid()) { connectionState.value = "retrying"; error.value = "实时连接中断，状态同步可能延迟。"; } };
    source.onopen = () => { if (valid()) void refreshCurrent().catch(() => undefined); };
  };

  const ensureDetail = () => {
    const capture = current.value;
    if (!capture || terminal(capture)) { closeDetail(); return; }
    if (!started || !origin || !sourceFactory || detailId === capture.smart_capture_id) return;
    closeDetail();
    detailId = capture.smart_capture_id;
    detailSource = sourceFactory(`${origin}/api/fine-job/smart-captures/${encodeURIComponent(detailId)}/events`);
    attach(detailSource, false, detailId);
  };

  const startRealtime = async () => {
    if (started) return;
    started = true;
    const ownGeneration = generation;
    connectionState.value = "connecting";
    void refreshCurrent().catch(() => undefined);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      origin = await Promise.race([(dependencies.getOrigin ?? getBackendOrigin)(), new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error("后端地址读取超时。")), 15000); })]);
      if (!started || ownGeneration !== generation || !sourceFactory) return;
      currentSource = sourceFactory(`${origin}/api/fine-job/smart-captures/current/events`);
      attach(currentSource, true);
      ensureDetail();
    } catch (value) {
      if (ownGeneration === generation) {
        error.value = (value as Error).message;
        connectionState.value = "retrying";
        started = false;
        if (recoveryTimer) clearTimeout(recoveryTimer);
        recoveryTimer = setTimeout(() => {
          recoveryTimer = null;
          if (ownGeneration === generation) void startRealtime();
        }, [1000, 2000, 5000, 10000, 15000][Math.min(failures++, 4)]);
      }
    } finally { if (timer) clearTimeout(timer); }
  };

  const stopRealtime = () => {
    started = false;
    generation += 1;
    currentSource?.close(); currentSource = null;
    closeDetail();
    if (recoveryTimer) clearTimeout(recoveryTimer);
    recoveryTimer = null; refreshing = null; loading.value = false;
  };
  const subscribe = (listener: (capture: FineJobSmartCapture | null) => void) => {
    listeners.add(listener); listener(current.value);
    return () => { listeners.delete(listener); };
  };
  return { current, loading, loaded, error, connectionState, refreshCurrent, startRealtime, stopRealtime, apply, subscribe, getCurrent: () => current.value };
};

export const useFineJobSmartCaptureStore = defineStore("fineJobSmartCapture", () => createSmartCaptureRealtime());
