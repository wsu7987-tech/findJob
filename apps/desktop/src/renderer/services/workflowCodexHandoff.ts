import type { FineJobWorkflowRun } from "@/types";

import { ApiError, api } from "./api";
import { getCodexBridge } from "./desktop-bridge";

type HandoffTrigger = "auto" | "manual";

export type WorkflowCodexStore = {
  status: string;
  runtimeId: string | null;
  sessionRef: string | null;
  startWorkflow: (launch: {
    cols: number;
    rows: number;
    model: string;
    reasoningEffort: string;
    sessionRef?: string;
  }) => Promise<{
    runtimeId: string | null;
    sessionRef: string | null;
    workflowSessionMode?: "live_reused" | "resumed_explicit" | "new_from_workflow_state";
  }>;
};

type WorkflowHandoffApi = Pick<
  typeof api,
  | "claimFineJobWorkflowAnalysisHandoff"
  | "markFineJobWorkflowAnalysisHandoffPromptWritten"
  | "releaseFineJobWorkflowAnalysisHandoff"
  | "getFineJobWorkflowRun"
>;

type PromptTransport = {
  submitWorkflowCodexPrompt?: (prompt: string) => Promise<boolean>;
  submitWorkflowCodexKey?: () => Promise<boolean>;
};

export type WorkflowCodexHandoffResult = {
  status: "submitted" | "enter_submitted" | "skipped" | "transport_failed" | "transport_unconfirmed" | "busy";
  run: FineJobWorkflowRun;
  message: string;
};

const activeHandoffs = new Map<string, Promise<WorkflowCodexHandoffResult>>();

const executionPolicy = (run: FineJobWorkflowRun) => run.completion_contract?.execution_policy ?? {};

const hasReadyBatch = (run: FineJobWorkflowRun) => {
  const handoff = run.analysis_handoff;
  if (!handoff || handoff.pending_item_count <= 0) return false;
  if (!["none", "released"].includes(handoff.attempt_status)) return false;
  return handoff.needs_initial_codex_handoff || handoff.needs_next_batch_handoff;
};

const canStartHandoff = (run: FineJobWorkflowRun, trigger: HandoffTrigger) => {
  if (run.status !== "waiting_codex" || run.waiting_for_user) return false;
  if (trigger === "auto") {
    return executionPolicy(run).codex_handoff !== "manual" && hasReadyBatch(run);
  }
  return hasReadyBatch(run);
};

const buildWorkflowPrompt = (
  workflowRunId: string,
  analysisBatchId: string,
  handoffAttemptId: string
) => [
  "使用 $finejob 处理 deep_job_search Workflow。",
  `workflow_run_id=${workflowRunId}`,
  `analysis_batch_id=${analysisBatchId}`,
  `handoff_attempt_id=${handoffAttemptId}`,
  "第一个正式动作必须调用 finejob.ack_workflow_analysis_batch_started，并携带以上三个标识；ACK 成功后再读取 Workflow 状态、待分析 Item 和单 Item 上下文，按 FineJob Skill 保存当前批次结果。"
].join("，");

const currentRun = async (client: WorkflowHandoffApi, workflowRunId: string, fallback: FineJobWorkflowRun) => {
  try {
    return await client.getFineJobWorkflowRun(workflowRunId);
  } catch {
    return fallback;
  }
};

const runHandoff = async (
  run: FineJobWorkflowRun,
  codexStore: WorkflowCodexStore,
  trigger: HandoffTrigger,
  client: WorkflowHandoffApi,
  transport: PromptTransport
): Promise<WorkflowCodexHandoffResult> => {
  if (!canStartHandoff(run, trigger)) {
    return { status: "skipped", run, message: "当前 Run 没有可交接的 Analysis Batch。" };
  }
  const execution = run.completion_contract?.codex_execution_config;
  if (!execution?.model || !execution.reasoning_effort || !transport.submitWorkflowCodexPrompt) {
    return { status: "skipped", run, message: "当前桌面端缺少可用的 Codex handoff 条件。" };
  }
  if (codexStore.status === "running" && codexStore.sessionRef !== run.codex_session_ref) {
    return { status: "busy", run, message: "当前 Codex 会话属于其他任务，等待该会话结束后再交接。" };
  }

  const session = await codexStore.startWorkflow({
    cols: 120,
    rows: 36,
    model: execution.model,
    reasoningEffort: execution.reasoning_effort,
    sessionRef: run.codex_session_ref || undefined
  });
  if (!session.sessionRef) {
    return { status: "busy", run, message: "Codex 会话未返回可绑定的 Session Ref。" };
  }

  const handoff = run.analysis_handoff!;
  let claimed: FineJobWorkflowRun;
  try {
    claimed = await client.claimFineJobWorkflowAnalysisHandoff(run.workflow_run_id, {
      codex_session_ref: session.sessionRef,
      codex_runtime_id: session.runtimeId || undefined,
      handoff_kind: handoff.needs_next_batch_handoff ? "next" : "initial",
    });
  } catch (error) {
    if (error instanceof ApiError && error.statusCode === 409) {
      const refreshed = await currentRun(client, run.workflow_run_id, run);
      return { status: "skipped", run: refreshed, message: "当前批次已由其他入口交接给 Codex。" };
    }
    throw error;
  }
  const claimedHandoff = claimed.analysis_handoff;
  const analysisBatchId = claimedHandoff?.analysis_batch_id || "";
  const handoffAttemptId = claimedHandoff?.handoff_attempt_id || "";
  if (!analysisBatchId || !handoffAttemptId) {
    return { status: "transport_unconfirmed", run: claimed, message: "后端没有返回有效的分析交接尝试。" };
  }

  // 直接调用工作流提交通道，由会话层完成 Prompt 写入、等待和提交键发送。
  const submitted = await transport.submitWorkflowCodexPrompt(
    buildWorkflowPrompt(run.workflow_run_id, analysisBatchId, handoffAttemptId)
  );
  if (!submitted) {
    try {
      const released = await client.releaseFineJobWorkflowAnalysisHandoff(run.workflow_run_id, {
        analysis_batch_id: analysisBatchId,
        handoff_attempt_id: handoffAttemptId,
        codex_session_ref: session.sessionRef
      });
      return { status: "transport_failed", run: released, message: "Codex 终端当前不可接收 Prompt，交接已释放。" };
    } catch {
      return {
        status: "transport_failed",
        run: await currentRun(client, run.workflow_run_id, claimed),
        message: "Codex 终端未确认接收 Prompt，交接状态以服务端为准。"
      };
    }
  }

  try {
    const promptWritten = await client.markFineJobWorkflowAnalysisHandoffPromptWritten(run.workflow_run_id, {
      analysis_batch_id: analysisBatchId,
      handoff_attempt_id: handoffAttemptId,
      codex_session_ref: session.sessionRef
    });
    return { status: "submitted", run: promptWritten, message: "Prompt 已写入并提交给 Codex，等待当前 attempt 的开始 ACK。" };
  } catch {
    return {
      status: "transport_unconfirmed",
      run: await currentRun(client, run.workflow_run_id, claimed),
      message: "Prompt 已写入并提交终端，等待服务端确认 transport 状态。"
    };
  }
};

export const triggerWorkflowCodexHandoff = (
  run: FineJobWorkflowRun,
  codexStore: WorkflowCodexStore,
  trigger: HandoffTrigger = "manual",
  dependencies: { client?: WorkflowHandoffApi; transport?: PromptTransport } = {}
) => {
  // 同一 renderer 内的并发入口共享一次交接，跨入口竞争仍由后端原子 claim 收口。
  const existing = activeHandoffs.get(run.workflow_run_id);
  if (existing) return existing;
  const task = runHandoff(
    run,
    codexStore,
    trigger,
    dependencies.client ?? api,
    dependencies.transport ?? getCodexBridge()
  ).finally(() => activeHandoffs.delete(run.workflow_run_id));
  activeHandoffs.set(run.workflow_run_id, task);
  return task;
};

const hasLivePromptWrittenAttempt = (run: FineJobWorkflowRun, codexStore: WorkflowCodexStore) => {
  const handoff = run.analysis_handoff;
  return Boolean(
    run.status === "waiting_codex"
      && handoff?.attempt_status === "prompt_written"
      && handoff.codex_session_ref
      && handoff.codex_session_ref === codexStore.sessionRef
      && run.codex_session_ref === codexStore.sessionRef
      && codexStore.status === "running"
  );
};

export const resubmitWorkflowCodexSubmit = async (
  run: FineJobWorkflowRun,
  codexStore: WorkflowCodexStore,
  transport: PromptTransport = getCodexBridge() ?? {}
): Promise<WorkflowCodexHandoffResult> => {
  if (!hasLivePromptWrittenAttempt(run, codexStore) || !transport.submitWorkflowCodexKey) {
    return { status: "skipped", run, message: "当前交接不能再次提交；请查看 Codex 或在超时后重新交接。" };
  }
  // 仅发送当前 Workflow 会话的专用提交键，attempt 与业务状态均保持不变。
  const submitted = await transport.submitWorkflowCodexKey();
  return submitted
    ? { status: "enter_submitted", run, message: "已再次发送 Workflow 提交键，等待当前 attempt 的开始 ACK。" }
    : { status: "transport_failed", run, message: "当前 Codex 会话不可发送 Workflow 提交键，请查看会话或重新交接。" };
};

export const retryWorkflowCodexHandoff = (
  run: FineJobWorkflowRun,
  codexStore: WorkflowCodexStore,
  dependencies: { client?: WorkflowHandoffApi; transport?: PromptTransport } = {}
) => {
  const existing = activeHandoffs.get(run.workflow_run_id);
  if (existing) return existing;
  const client = dependencies.client ?? api;
  const transport = dependencies.transport ?? getCodexBridge() ?? {};
  const task = (async (): Promise<WorkflowCodexHandoffResult> => {
    const handoff = run.analysis_handoff;
    if (
      run.status !== "waiting_codex"
      || handoff?.attempt_status !== "prompt_written"
      || !handoff.retry_available
      || !handoff.analysis_batch_id
      || !handoff.handoff_attempt_id
      || !handoff.codex_session_ref
    ) {
      return { status: "skipped", run, message: "当前交接尚未进入可重新交接状态。" };
    }
    const released = await client.releaseFineJobWorkflowAnalysisHandoff(run.workflow_run_id, {
      analysis_batch_id: handoff.analysis_batch_id,
      handoff_attempt_id: handoff.handoff_attempt_id,
      codex_session_ref: handoff.codex_session_ref,
      release_reason: "full_retry"
    });
    return runHandoff(released, codexStore, "manual", client, transport);
  })().finally(() => activeHandoffs.delete(run.workflow_run_id));
  activeHandoffs.set(run.workflow_run_id, task);
  return task;
};

export const isAutoWorkflowCodexHandoffReady = (run: FineJobWorkflowRun | null) =>
  Boolean(run && canStartHandoff(run, "auto"));
