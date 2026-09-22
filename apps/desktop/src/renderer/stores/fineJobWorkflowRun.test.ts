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

  it("创建后通过 child start endpoint 启动岗位采集，不调用旧 advance", async () => {
    const pendingRun = {
      ...run("pending"),
      children: [{
        child_relation_id: "relation-1",
        child_type: "smart_capture",
        status: "pending",
      }],
    };
    const runningRun = { ...run("running"), children: pendingRun.children };
    const create = vi.spyOn(api, "createFineJobDeepJobSearchRun").mockResolvedValue(pendingRun as never);
    const startChild = vi.spyOn(api, "startFineJobWorkflowChild").mockResolvedValue(runningRun as never);
    const advance = vi.spyOn(api, "advanceFineJobWorkflowRun").mockResolvedValue(runningRun as never);
    const store = useFineJobWorkflowRunStore();

    await store.create({} as never);
    store.stopPolling();

    expect(create).toHaveBeenCalled();
    expect(startChild).toHaveBeenCalledWith("workflow-1", "relation-1");
    expect(advance).not.toHaveBeenCalled();
  });

  it("收到较旧 state_version 的 SSE 快照时保留当前父子投影", () => {
    const store = useFineJobWorkflowRunStore();
    store.setRun({ ...run("running"), state_version: 4 } as never);

    store.setRun({ ...run("paused"), state_version: 3 } as never);

    expect(store.currentRun?.state_version).toBe(4);
    expect(store.currentRun?.control_state).toBe("active");
  });

  it("恢复子任务后回读父快照，保持驾驶舱父子投影同步", async () => {
    const resumeChild = vi.spyOn(api, "resumeFineJobSmartCapture").mockResolvedValue({} as never);
    const refreshed = run("waiting_for_user");
    const getRun = vi.spyOn(api, "getFineJobWorkflowRun").mockResolvedValue(refreshed as never);
    const store = useFineJobWorkflowRunStore();
    store.setRun({ ...refreshed, control_state: "waiting_child_interrupted" } as never);

    await store.resumeChild({ child_ref: "capture-1", capabilities: { resume: true } } as never);

    expect(resumeChild).toHaveBeenCalledWith("capture-1");
    expect(getRun).toHaveBeenCalledWith("workflow-1");
    expect(store.currentRun?.status).toBe("waiting_for_user");
  });
});
