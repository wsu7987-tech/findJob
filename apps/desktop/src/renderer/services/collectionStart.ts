import { reactive } from "vue";

export type CollectionStartReceipt = {
  response_type: "operation_receipt";
  operation_id: string;
  action: string;
  status: "pending" | "started" | "rejected" | "unknown";
  phase: string;
  result_kind?: string | null;
  result_task_id?: string | null;
  result_summary?: Record<string, unknown>;
  message?: string;
};
type Intent = { operation_id: string; action: string; owner: string; submitted_at: string };

export const createCollectionStartCoordinator = (dependencies: {
  request: <T>(path: string, init?: RequestInit & { timeoutMs?: number }) => Promise<T>;
  getOrigin: () => Promise<string>;
}) => {
  const state = reactive({ intent: null as Intent | null, receipt: null as CollectionStartReceipt | null, submitting: false, checking: false, error: "" });
  let storageKey = "";
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempts = 0;
  let querying: Promise<CollectionStartReceipt | null> | null = null;
  let disposed = false;
  const initialize = async () => {
    if (storageKey) return;
    let budget: ReturnType<typeof setTimeout> | undefined;
    try {
      const origin = await Promise.race([dependencies.getOrigin(), new Promise<never>((_, reject) => { budget = setTimeout(() => reject(new Error("后端地址读取超时。")), 15000); })]);
      storageKey = `finejob:collection-start:${origin}`;
      const saved = localStorage.getItem(storageKey);
      if (saved) state.intent = JSON.parse(saved) as Intent;
    } finally { if (budget) clearTimeout(budget); }
  };
  const settle = (receipt: CollectionStartReceipt) => {
    if (state.intent?.operation_id !== receipt.operation_id) return;
    state.receipt = receipt;
    if (receipt.status === "started" || receipt.status === "rejected") {
      state.intent = null;
      localStorage.removeItem(storageKey);
      if (timer) clearTimeout(timer);
      timer = null;
    }
    state.error = receipt.status === "rejected" ? receipt.message || "启动请求已拒绝。" : "";
  };
  const schedule = () => {
    if (disposed || timer || !state.intent || (!state.submitting && attempts >= 6)) return;
    const delay = state.submitting ? 2000 : [1000, 2000, 5000, 10000, 15000, 15000][attempts++];
    timer = setTimeout(() => { timer = null; void check(); }, delay);
  };
  const check = (): Promise<CollectionStartReceipt | null> => {
    if (querying) return querying;
    if (!state.intent) return Promise.resolve(state.receipt);
    const operationId = state.intent.operation_id;
    state.checking = true;
    querying = (async () => {
      try {
        const receipt = await dependencies.request<CollectionStartReceipt>(`/api/fine-job/collection-start-operations/${operationId}`);
        settle(receipt);
        return receipt;
      } catch (error) {
        if (state.intent?.operation_id === operationId) state.error = `启动结果待确认：${(error as Error).message}`;
        return null;
      } finally { state.checking = false; querying = null; schedule(); }
    })();
    return querying;
  };
  const loadResult = async <T>(receipt: CollectionStartReceipt): Promise<T> => {
    const collection = receipt.result_kind === "smart" ? "smart-captures" : receipt.result_kind === "workflow" ? "workflow-runs" : "boss-capture/tasks";
    try { return await dependencies.request<T>(`/api/fine-job/${collection}/${encodeURIComponent(receipt.result_task_id || "")}`); }
    catch (error) { state.error = `启动已受理，状态同步失败：${(error as Error).message}`; throw new Error(state.error); }
  };
  const start = async <T>(path: string, payload: object, action: string, owner = ""): Promise<T> => {
    if (state.submitting) throw new Error("启动请求正在提交。");
    state.submitting = true;
    state.error = "";
    let submittedId = "";
    try {
      await initialize();
      if (state.intent) { void check(); throw new Error("上次启动结果待确认，请先检查启动结果。"); }
      const intent = { operation_id: crypto.randomUUID(), action, owner, submitted_at: new Date().toISOString() };
      submittedId = intent.operation_id;
      state.intent = intent;
      state.receipt = null;
      // 发送前保存身份，刷新、换页和应用重开均可继续确认同一请求。
      localStorage.setItem(storageKey, JSON.stringify(intent));
      attempts = 0;
      schedule();
      const response = await dependencies.request<T | CollectionStartReceipt>(path, { method: "POST", body: JSON.stringify({ ...payload, operation_id: intent.operation_id }), timeoutMs: 180000 });
      if ((response as CollectionStartReceipt).response_type === "operation_receipt") {
        const receipt = response as CollectionStartReceipt;
        settle(receipt);
        if (receipt.status === "started") return await loadResult<T>(receipt);
        throw new Error(receipt.message || "启动结果待确认，请检查启动结果。");
      }
      if (state.intent?.operation_id === intent.operation_id) {
        state.intent = null; localStorage.removeItem(storageKey);
      }
      return response as T;
    } catch (error) {
      state.error = (error as Error).message;
      if (state.receipt?.operation_id === submittedId && state.receipt.status === "started") return await loadResult<T>(state.receipt);
      if (state.intent) {
        const receipt = await check();
        if (receipt?.status === "started") return await loadResult<T>(receipt);
      }
      throw error;
    } finally {
      state.submitting = false;
      if (timer) clearTimeout(timer);
      timer = null;
      schedule();
    }
  };
  const restore = async () => { disposed = false; await initialize(); if (state.intent) { attempts = 0; await check(); } };
  const resolve = async () => {
    if (!state.intent || state.checking) return;
    state.checking = true;
    try { settle(await dependencies.request<CollectionStartReceipt>(`/api/fine-job/collection-start-operations/${state.intent.operation_id}/resolve`, { method: "POST", timeoutMs: 15000 })); }
    catch (error) { state.error = (error as Error).message; }
    finally { state.checking = false; }
  };
  const dispose = () => { disposed = true; if (timer) clearTimeout(timer); timer = null; };
  return { state, start, check, resolve, restore, loadResult, dispose };
};
