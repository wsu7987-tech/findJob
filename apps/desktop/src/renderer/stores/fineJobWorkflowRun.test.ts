import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import { useFineJobWorkflowRunStore } from "./fineJobWorkflowRun";

const run = (status: string) => ({
  workflow_run_id: "workflow-1",
  workflow_type: "deep_job_search" as const,
  status,
  control_state: status === "paused" ? "paused" : "active",
  waiting_reason: "",
  control_cause: status === "paused" ? "parent_pause" : "",
  state_version: 3,
  transition_id: "transition-1",
  completed_count: 0,
  remaining_count: 1,
  current_step: status,
  next_action: "",
  next_action_reason: "",
  waiting_for_user: false,
  stop_reason: "",
  telemetry: {},
  progress: {},
});

describe("fineJobWorkflowRun store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("恢复父任务时只调用一个 resume endpoint，不追加 advance", async () => {
    const resume = vi.spyOn(api, "resumeFineJobWorkflowRun").mockResolvedValue(run("running") as never);
    const advance = vi.spyOn(api, "advanceFineJobWorkflowRun").mockResolvedValue(run("running") as never);
    const store = useFineJobWorkflowRunStore();
    store.setRun(run("paused") as never);

    await store.resume();

    expect(resume).toHaveBeenCalledWith("workflow-1");
    expect(advance).not.toHaveBeenCalled();
  });
});
