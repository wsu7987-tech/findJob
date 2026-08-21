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

    expect(api.listFineJobReviewItems).toHaveBeenCalledWith("pending");
    expect(api.listFineJobAutomationActions).toHaveBeenCalledWith("queued");
    expect(store.items[0].evaluation.evaluation_version).toBe("2.0");
  });

  it("approves edited greeting and refreshes the current list", async () => {
    vi.spyOn(api, "approveFineJobReviewItem").mockResolvedValue({
      action: {
        id: "action-1",
        job_id: "history-job-1",
        evaluation_id: "evaluation-1",
        review_item_id: "review-1",
        action_type: "start_conversation",
        status: "queued",
        idempotency_key: "boss:history-job-1:start_conversation",
        payload: { message: "编辑后的招呼语" },
        attempt_count: 0,
        job_title: "Python 开发",
        company_name: "示例科技",
        created_at: "2026-08-21T10:00:00Z",
        updated_at: "2026-08-21T10:00:00Z"
      }
    });
    vi.spyOn(api, "listFineJobReviewItems").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "listFineJobAutomationActions").mockResolvedValue({
      actions: [],
      total: 0
    });
    const store = useFineJobWorkflowStore();

    const action = await store.approve(reviewItem(), "编辑后的招呼语");

    expect(api.approveFineJobReviewItem).toHaveBeenCalledWith("review-1", {
      message: "编辑后的招呼语",
      allow_override: false
    });
    expect(action.status).toBe("queued");
  });
});
