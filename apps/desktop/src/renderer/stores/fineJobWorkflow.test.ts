import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import type { FineJobReviewItem } from "@/types";
import { useFineJobWorkflowStore } from "./fineJobWorkflow";

const reviewItem = (): FineJobReviewItem => ({
  id: "review-1",
  job_id: "history-job-1",
  evaluation_id: "evaluation-1",
  action_type: "start_conversation",
  status: "pending",
  ai_decision: "recommend",
  draft_message: "您好，我对该岗位很感兴趣。",
  final_message: "",
  resolution_note: "",
  auto_approved: false,
  job_title: "Python 开发",
  company_name: "示例科技",
  job_link: "https://www.zhipin.com/job_detail/job-1.html",
  evaluation: {
    evaluation_version: "2.0",
    job_id: "job-1",
    decision: "recommend",
    confidence: 0.86,
    summary: "技能匹配",
    reasons: ["Python 匹配"],
    risks: [],
    missing_fields: [],
    missing_information: [],
    hard_requirements: [],
    match_dimensions: { core_skills: 0.9 },
    strengths: ["Python 匹配"],
    gaps: [],
    resume_suggestions: [],
    greeting_draft: {
      status: "ready",
      text: "您好，我对该岗位很感兴趣。",
      facts_used: []
    },
    source: "llm"
  },
  created_at: "2026-08-21T10:00:00Z",
  updated_at: "2026-08-21T10:00:00Z"
});

describe("fineJobWorkflow store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("loads review items and queued actions together", async () => {
    vi.spyOn(api, "listFineJobReviewItems").mockResolvedValue({
      items: [reviewItem()],
      total: 1
    });
    vi.spyOn(api, "listFineJobAutomationActions").mockResolvedValue({
      actions: [],
      total: 0
    });
    const store = useFineJobWorkflowStore();

    await store.load("pending");

    expect(api.listFineJobReviewItems).toHaveBeenCalledWith({
      status: "pending",
      execution_view: "",
      decision: "",
      query: "",
      execution_state: "",
      created_from: undefined,
      created_to: undefined,
      page: 1,
      page_size: 20
    });
    expect(api.listFineJobAutomationActions).toHaveBeenCalledWith("queued");
    expect(store.items[0].evaluation.evaluation_version).toBe("2.0");
  });

  it("批准默认招呼动作并刷新当前列表", async () => {
    vi.spyOn(api, "approveFineJobReviewItem").mockResolvedValue({
      action: {
        id: "action-1",
        job_id: "history-job-1",
        evaluation_id: "evaluation-1",
        review_item_id: "review-1",
        action_type: "BOSS_DEFAULT_GREETING",
        status: "queued",
        idempotency_key: "boss:history-job-1:start_conversation",
        payload: { message: "" },
        job_title: "Python 开发",
        company_name: "示例科技",
        created_at: "2026-08-21T10:00:00Z",
        updated_at: "2026-08-21T10:00:00Z",
        execution_state: "queued",
        execution_epoch: 0,
        result: {}
      }
    });
    vi.spyOn(api, "listFineJobReviewItems").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "listFineJobAutomationActions").mockResolvedValue({
      actions: [],
      total: 0
    });
    const store = useFineJobWorkflowStore();

    const action = await store.approve(reviewItem(), "");

    expect(api.approveFineJobReviewItem).toHaveBeenCalledWith("review-1", {
      message: "",
      allow_override: false
    });
    expect(action.status).toBe("queued");
  });

  it("携带筛选条件执行批量归档并刷新列表", async () => {
    vi.spyOn(api, "batchFineJobReviewItems").mockResolvedValue({
      results: [{ review_item_id: "review-1", success: true, error_message: "" }],
      succeeded: 1,
      failed: 0
    });
    vi.spyOn(api, "listFineJobReviewItems").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "listFineJobAutomationActions").mockResolvedValue({ actions: [], total: 0 });
    const store = useFineJobWorkflowStore();
    store.query = "Python";
    store.decision = "recommend";

    const result = await store.batch(["review-1"], "archive");

    expect(result.succeeded).toBe(1);
    expect(api.batchFineJobReviewItems).toHaveBeenCalledWith({
      review_item_ids: ["review-1"],
      operation: "archive",
      allow_override: false
    });
    expect(api.listFineJobReviewItems).toHaveBeenLastCalledWith(expect.objectContaining({
      query: "Python",
      decision: "recommend"
    }));
  });
});
