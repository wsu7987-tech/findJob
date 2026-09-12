import { describe, expect, it, vi } from "vitest";

import type { FineJobWorkflowRun } from "@/types";

import { createFineJobWorkflowCodexController } from "./fineJobWorkflowCodexController";

const waitingRun = (): FineJobWorkflowRun => ({
  workflow_run_id: "workflow-run-away-from-cockpit",
  workflow_type: "deep_job_search", status: "waiting_codex", completed_count: 0, remaining_count: 1,
  current_step: "waiting_codex", next_action: "codex_analysis", next_action_reason: "", waiting_for_user: false,
  stop_reason: "", telemetry: {},
  progress: { current_keyword: "", current_city: "", search_depth: 0, search_batch_count: 0, jobs_seen: 0, fresh_jobs: 0, duplicate_jobs: 0, candidates: 0, current_batch_new_jobs: 0, current_batch_duplicates: 0, jd_total: 0, jd_completed: 0, recommend_count: 0, review_count: 0, reject_count: 0 },
  completion_contract: { codex_execution_config: { model: "gpt-5.6-luna", reasoning_effort: "medium" }, execution_policy: { codex_handoff: "auto" } },
  analysis_handoff: { analysis_batch_id: "batch-away", handoff_attempt_id: "attempt-away", pending_item_count: 1, running_item_count: 0, succeeded_item_count: 0, handoff_status: "none", attempt_status: "none", needs_initial_codex_handoff: true, needs_next_batch_handoff: false, codex_processing: false, analysis_batch_complete: false, recovery_available: false, awaiting_start_ack: false, start_ack_timed_out: false, retry_available: false, start_ack_timeout_seconds: 45 }
});

describe("fineJobWorkflowCodexController", () => {
  it("不依赖 TaskCockpit mount，在其他 FineJob 页面也会发现 ready batch", async () => {
    const run = waitingRun();
    const getLatestRun = vi.fn().mockResolvedValue(run);
    const setRun = vi.fn();
    const codexStore = {
      load: vi.fn().mockResolvedValue(undefined), status: "idle", runtimeId: null, sessionRef: null,
      startWorkflow: vi.fn().mockResolvedValue({ runtimeId: "runtime-away", sessionRef: "runtime:away" })
    };
    const controller = createFineJobWorkflowCodexController({
      getLatestRun,
      workflowStore: { setRun },
      codexStore,
      intervalMs: 10_000,
      handoffDependencies: {
        client: {
          claimFineJobWorkflowAnalysisHandoff: vi.fn().mockResolvedValue(run),
          markFineJobWorkflowAnalysisHandoffPromptWritten: vi.fn().mockResolvedValue(run),
          releaseFineJobWorkflowAnalysisHandoff: vi.fn().mockResolvedValue(run),
          getFineJobWorkflowRun: vi.fn().mockResolvedValue(run)
        },
        transport: { submitCodexPrompt: vi.fn().mockResolvedValue(true) }
      }
    });

    await controller.tick();

    expect(getLatestRun).toHaveBeenCalledTimes(1);
    expect(codexStore.startWorkflow).toHaveBeenCalledTimes(1);
    expect(setRun).toHaveBeenCalled();
  });
});
