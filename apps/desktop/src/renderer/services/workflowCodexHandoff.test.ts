import { describe, expect, it, vi } from "vitest";

import type { FineJobSmartCaptureAnalysisSnapshot, FineJobWorkflowRun } from "@/types";

import {
  resubmitWorkflowCodexSubmit,
  retryWorkflowCodexHandoff,
  triggerWorkflowCodexHandoff,
  triggerSmartCaptureCodexHandoff
} from "./workflowCodexHandoff";

const run = (updates: Partial<FineJobWorkflowRun> = {}): FineJobWorkflowRun => ({
  workflow_run_id: "workflow-run-1",
  workflow_type: "deep_job_search",
  status: "waiting_codex",
  completed_count: 0,
  remaining_count: 2,
  current_step: "waiting_codex",
  next_action: "codex_analysis",
  next_action_reason: "等待 Codex",
  waiting_for_user: false,
  stop_reason: "",
  telemetry: {},
  progress: {
    current_keyword: "", current_city: "", search_depth: 0, search_batch_count: 0,
    jobs_seen: 0, fresh_jobs: 0, duplicate_jobs: 0, candidates: 0,
    current_batch_new_jobs: 0, current_batch_duplicates: 0, jd_total: 0, jd_completed: 0,
    recommend_count: 0, review_count: 0, reject_count: 0
  },
  completion_contract: {
    codex_execution_config: { model: "gpt-5.6-luna", reasoning_effort: "high" },
    execution_policy: { after_analysis_batch: "auto_continue", codex_handoff: "auto" }
  },
  analysis_handoff: {
    analysis_batch_id: "analysis-batch-1",
    handoff_attempt_id: "attempt-1",
    pending_item_count: 2,
    running_item_count: 0,
    succeeded_item_count: 0,
    handoff_status: "none",
    attempt_status: "none",
    needs_initial_codex_handoff: true,
    needs_next_batch_handoff: false,
    codex_processing: false,
    analysis_batch_complete: false,
    recovery_available: false,
    awaiting_start_ack: false,
    start_ack_timed_out: false,
    retry_available: false,
    start_ack_timeout_seconds: 45
  },
  ...updates
});

const dependencies = (returnedRun: FineJobWorkflowRun) => {
  const client = {
    claimFineJobWorkflowAnalysisHandoff: vi.fn().mockResolvedValue(returnedRun),
    markFineJobWorkflowAnalysisHandoffPromptWritten: vi.fn().mockResolvedValue(returnedRun),
    releaseFineJobWorkflowAnalysisHandoff: vi.fn().mockResolvedValue(returnedRun),
    getFineJobWorkflowRun: vi.fn().mockResolvedValue(returnedRun)
  };
  const transport = {
    submitWorkflowCodexPrompt: vi.fn().mockResolvedValue(true),
    submitWorkflowCodexKey: vi.fn().mockResolvedValue(true)
  };
  return { client, transport };
};

const codexStore = (sessionRef = "runtime:workflow-runtime-1") => ({
  status: "idle",
  runtimeId: null,
  sessionRef: null,
  startWorkflow: vi.fn().mockResolvedValue({ runtimeId: "runtime-1", sessionRef })
});

const smartSnapshot = (): FineJobSmartCaptureAnalysisSnapshot => ({
  smart_capture_id: "smart-capture-independent-1",
  workflow_run_id: null,
  status: "waiting_for_user",
  analysis_batch_id: "analysis-batch-1",
  items: [{ status: "pending", payload: { codex_model: "gpt-5.6-luna", codex_reasoning_effort: "high" } } as never],
  handoff: {
    analysis_batch_id: "analysis-batch-1",
    pending_item_count: 1,
    running_item_count: 0,
    succeeded_item_count: 0,
    handoff_status: "none",
    attempt_status: "none",
    needs_initial_codex_handoff: true,
    needs_next_batch_handoff: false
  },
  smart_capture: {
    smart_capture_id: "smart-capture-independent-1",
    source: "boss_capture",
    workflow_run_id: null,
    status: "waiting_for_user",
    search_config: {},
    execution_config: {
      analysis: { codex_model: "gpt-5.6-luna", codex_reasoning_effort: "high", handoff: "auto" }
    },
    stage: "waiting_codex",
    waiting_reason: "codex",
    control_cause: "",
    state_version: 2,
    capabilities: { start: false, pause: false, resume: false, retry: false, stop: true },
    progress: {},
    result_summary: {},
    message: "",
    created_at: "",
    updated_at: "",
    batches: [],
    jobs: []
  }
});

describe("workflowCodexHandoff", () => {
  it("auto 模式为首个 ready batch 创建会话、claim 并写入 Prompt", async () => {
    const source = run();
    const written = run({
      codex_session_ref: "runtime:workflow-runtime-1",
      analysis_handoff: { ...source.analysis_handoff!, attempt_status: "prompt_written", handoff_status: "submitted" }
    });
    const handoffDependencies = dependencies(written);
    const codex = codexStore();

    const result = await triggerWorkflowCodexHandoff(source, codex, "auto", handoffDependencies);

    expect(result.status).toBe("submitted");
    expect(codex.startWorkflow).toHaveBeenCalledWith(expect.objectContaining({ model: "gpt-5.6-luna" }));
    expect(handoffDependencies.client.claimFineJobWorkflowAnalysisHandoff).toHaveBeenCalledWith("workflow-run-1", expect.objectContaining({ handoff_kind: "initial" }));
    expect(handoffDependencies.transport.submitWorkflowCodexPrompt).toHaveBeenCalledWith(expect.stringContaining("handoff_attempt_id=attempt-1"));
    expect(handoffDependencies.client.markFineJobWorkflowAnalysisHandoffPromptWritten).toHaveBeenCalledTimes(1);
  });

  it("auto 模式为下一批沿用同一 Workflow 会话并使用 next claim", async () => {
    const source = run({
      codex_session_ref: "runtime:workflow-runtime-1",
      analysis_handoff: {
        ...run().analysis_handoff!, analysis_batch_id: "analysis-batch-2", handoff_attempt_id: "attempt-2",
        needs_initial_codex_handoff: false, needs_next_batch_handoff: true
      }
    });
    const handoffDependencies = dependencies(source);
    const codex = { ...codexStore("runtime:workflow-runtime-1"), status: "running", sessionRef: "runtime:workflow-runtime-1" };

    await triggerWorkflowCodexHandoff(source, codex, "auto", handoffDependencies);

    expect(codex.startWorkflow).toHaveBeenCalledWith(expect.objectContaining({ sessionRef: "runtime:workflow-runtime-1" }));
    expect(handoffDependencies.client.claimFineJobWorkflowAnalysisHandoff).toHaveBeenCalledWith("workflow-run-1", expect.objectContaining({ handoff_kind: "next" }));
  });

  it("manual 模式不被 auto controller 触发，但人工触发可交接", async () => {
    const source = run({ completion_contract: { ...run().completion_contract, execution_policy: { codex_handoff: "manual" } } });
    const handoffDependencies = dependencies(source);
    const codex = codexStore();

    const automatic = await triggerWorkflowCodexHandoff(source, codex, "auto", handoffDependencies);
    const manual = await triggerWorkflowCodexHandoff(source, codex, "manual", handoffDependencies);

    expect(automatic.status).toBe("skipped");
    expect(manual.status).toBe("submitted");
    expect(handoffDependencies.transport.submitWorkflowCodexPrompt).toHaveBeenCalledTimes(1);
  });

  it("已有有效 attempt、暂停或等待用户边界均不重复 transport", async () => {
    const handoffDependencies = dependencies(run());
    const codex = codexStore();
    const active = run({ analysis_handoff: { ...run().analysis_handoff!, attempt_status: "prompt_written" } });
    const paused = run({ status: "paused" });
    const cancelled = run({ status: "cancelled" });
    const waiting = run({ status: "waiting_for_user", waiting_for_user: true });

    await triggerWorkflowCodexHandoff(active, codex, "auto", handoffDependencies);
    await triggerWorkflowCodexHandoff(paused, codex, "auto", handoffDependencies);
    await triggerWorkflowCodexHandoff(cancelled, codex, "auto", handoffDependencies);
    await triggerWorkflowCodexHandoff(waiting, codex, "auto", handoffDependencies);

    expect(codex.startWorkflow).not.toHaveBeenCalled();
    expect(handoffDependencies.transport.submitWorkflowCodexPrompt).not.toHaveBeenCalled();
  });

  it("同一 renderer 的并发触发共享一次 handoff，后端 claim 保持最终防线", async () => {
    const source = run();
    const handoffDependencies = dependencies(source);
    const codex = codexStore();

    await Promise.all([
      triggerWorkflowCodexHandoff(source, codex, "auto", handoffDependencies),
      triggerWorkflowCodexHandoff(source, codex, "manual", handoffDependencies)
    ]);

    expect(codex.startWorkflow).toHaveBeenCalledTimes(1);
    expect(handoffDependencies.client.claimFineJobWorkflowAnalysisHandoff).toHaveBeenCalledTimes(1);
    expect(handoffDependencies.transport.submitWorkflowCodexPrompt).toHaveBeenCalledTimes(1);
  });

  it("prompt_written 只再次发送 Enter，不写 Prompt、claim 或创建 attempt", async () => {
    const source = run({
      codex_session_ref: "runtime:workflow-runtime-1",
      analysis_handoff: {
        ...run().analysis_handoff!,
        attempt_status: "prompt_written",
        codex_session_ref: "runtime:workflow-runtime-1"
      }
    });
    const handoffDependencies = dependencies(source);
    const codex = { ...codexStore(), status: "running", sessionRef: "runtime:workflow-runtime-1" };

    const result = await resubmitWorkflowCodexSubmit(source, codex, handoffDependencies.transport);

    expect(result.status).toBe("enter_submitted");
    expect(handoffDependencies.transport.submitWorkflowCodexKey).toHaveBeenCalledTimes(1);
    expect(handoffDependencies.transport.submitWorkflowCodexPrompt).not.toHaveBeenCalled();
    expect(handoffDependencies.client.claimFineJobWorkflowAnalysisHandoff).not.toHaveBeenCalled();
  });

  it("超时后的重新交接先释放旧 attempt，再创建并写入新 Prompt", async () => {
    const source = run({
      codex_session_ref: "runtime:closed-runtime-1",
      analysis_handoff: {
        ...run().analysis_handoff!,
        attempt_status: "prompt_written",
        codex_session_ref: "runtime:closed-runtime-1",
        retry_available: true
      }
    });
    const released = run({
      analysis_handoff: { ...source.analysis_handoff!, attempt_status: "released" }
    });
    const claimed = run({
      codex_session_ref: "runtime:workflow-runtime-2",
      analysis_handoff: {
        ...source.analysis_handoff!,
        handoff_attempt_id: "attempt-2",
        attempt_status: "claimed",
        codex_session_ref: "runtime:workflow-runtime-2"
      }
    });
    const promptWritten = run({
      codex_session_ref: "runtime:workflow-runtime-2",
      analysis_handoff: { ...claimed.analysis_handoff!, attempt_status: "prompt_written" }
    });
    const handoffDependencies = dependencies(promptWritten);
    handoffDependencies.client.releaseFineJobWorkflowAnalysisHandoff.mockResolvedValue(released);
    handoffDependencies.client.claimFineJobWorkflowAnalysisHandoff.mockResolvedValue(claimed);
    const codex = codexStore("runtime:workflow-runtime-2");

    const result = await retryWorkflowCodexHandoff(source, codex, handoffDependencies);

    expect(result.status).toBe("submitted");
    expect(handoffDependencies.client.releaseFineJobWorkflowAnalysisHandoff).toHaveBeenCalledWith("workflow-run-1", {
      analysis_batch_id: "analysis-batch-1",
      handoff_attempt_id: "attempt-1",
      codex_session_ref: "runtime:closed-runtime-1",
      release_reason: "full_retry"
    });
    expect(handoffDependencies.client.claimFineJobWorkflowAnalysisHandoff).toHaveBeenCalledTimes(1);
    expect(handoffDependencies.transport.submitWorkflowCodexPrompt).toHaveBeenCalledWith(expect.stringContaining("handoff_attempt_id=attempt-2"));
  });

  it("independent Smart Capture 使用 smart_capture_id 完成 attach、claim、Prompt transport", async () => {
    const source = smartSnapshot();
    const client = {
      getFineJobSmartCaptureAnalysisSnapshot: vi.fn().mockResolvedValue(source),
      attachFineJobSmartCaptureCodexSession: vi.fn().mockResolvedValue(source.smart_capture),
      claimFineJobSmartCaptureAnalysisHandoff: vi.fn().mockResolvedValue({
        ...source,
        handoff: { ...source.handoff!, attempt_status: "claimed", handoff_attempt_id: "attempt-smart-1" }
      }),
      markFineJobSmartCaptureAnalysisHandoffPromptWritten: vi.fn().mockResolvedValue({
        ...source,
        handoff: { ...source.handoff!, attempt_status: "prompt_written", handoff_attempt_id: "attempt-smart-1" }
      }),
      releaseFineJobSmartCaptureAnalysisHandoff: vi.fn().mockResolvedValue(source.smart_capture)
    };
    const transport = { submitSmartCaptureCodexPrompt: vi.fn().mockResolvedValue(true) };
    const codex = {
      status: "idle",
      runtimeId: null,
      sessionRef: null,
      startSmartCapture: vi.fn().mockResolvedValue({ runtimeId: "runtime-smart-1", sessionRef: "runtime:smart-1" })
    };

    const result = await triggerSmartCaptureCodexHandoff(source, codex, "manual", { client, transport });

    expect(result.status).toBe("submitted");
    expect(client.attachFineJobSmartCaptureCodexSession).toHaveBeenCalledWith("smart-capture-independent-1", expect.objectContaining({
      analysis_batch_id: "analysis-batch-1"
    }));
    expect(client.claimFineJobSmartCaptureAnalysisHandoff).toHaveBeenCalledWith("smart-capture-independent-1", expect.objectContaining({
      handoff_kind: "initial"
    }));
    expect(transport.submitSmartCaptureCodexPrompt).toHaveBeenCalledWith(expect.stringContaining("smart_capture_id=smart-capture-independent-1"));
    expect(transport.submitSmartCaptureCodexPrompt).not.toHaveBeenCalledWith(expect.stringContaining("workflow_run_id="));
  });

  it("linked Smart Capture 复用同一 transport，父 Workflow 仅保留导航关联", async () => {
    const base = smartSnapshot();
    const source: FineJobSmartCaptureAnalysisSnapshot = {
      ...base,
      workflow_run_id: "workflow-parent-1",
      smart_capture: {
        ...base.smart_capture,
        source: "task_cockpit",
        workflow_run_id: "workflow-parent-1"
      }
    };
    const client = {
      getFineJobSmartCaptureAnalysisSnapshot: vi.fn().mockResolvedValue(source),
      attachFineJobSmartCaptureCodexSession: vi.fn().mockResolvedValue(source.smart_capture),
      claimFineJobSmartCaptureAnalysisHandoff: vi.fn().mockResolvedValue({
        ...source,
        handoff: { ...source.handoff!, attempt_status: "claimed", handoff_attempt_id: "attempt-smart-linked-1" }
      }),
      markFineJobSmartCaptureAnalysisHandoffPromptWritten: vi.fn().mockResolvedValue(source),
      releaseFineJobSmartCaptureAnalysisHandoff: vi.fn().mockResolvedValue(source.smart_capture)
    };
    const transport = { submitSmartCaptureCodexPrompt: vi.fn().mockResolvedValue(true) };
    const codex = {
      status: "idle",
      runtimeId: null,
      sessionRef: null,
      startSmartCapture: vi.fn().mockResolvedValue({ runtimeId: "runtime-smart-linked-1", sessionRef: "runtime:smart-linked-1" })
    };

    await triggerSmartCaptureCodexHandoff(source, codex, "manual", { client, transport });

    expect(client.claimFineJobSmartCaptureAnalysisHandoff).toHaveBeenCalledWith(
      "smart-capture-independent-1",
      expect.objectContaining({ handoff_kind: "initial" })
    );
    expect(transport.submitSmartCaptureCodexPrompt).toHaveBeenCalledWith(
      expect.stringContaining("smart_capture_id=smart-capture-independent-1")
    );
    expect(transport.submitSmartCaptureCodexPrompt).not.toHaveBeenCalledWith(
      expect.stringContaining("workflow-parent-1")
    );
  });
});
