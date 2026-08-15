import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { ApiError, api } from "@/services/api";
import { useQaStore } from "./qa";

describe("qa store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("stores the latest answer payload and selected session after a successful request", async () => {
    vi.spyOn(api, "answerQuestion").mockResolvedValue({
      session_id: "session-1",
      mode: "answer",
      rewritten_question: "What is alpha?",
      rewrite: {
        rewritten_question: "What is alpha?",
        requires_history: false,
        used_history: false,
        intent: "answer",
        risk_flags: ["self_contained"],
        confidence: 0.72,
        strategy: "heuristic"
      },
      question: "What is alpha?",
      answer: "Alpha answer",
      answer_status: "grounded",
      confidence: 0.83,
      applied_filters: {
        source_types: ["text"],
        knowledge_item_ids: ["ki-1"],
        keyword: "alpha",
        category: "research",
        user_tags: ["alpha"],
        ai_tags: ["report"]
      },
      citations: [
        {
          citation_id: "cite-1",
          rank: 1,
          knowledge_item_id: "ki-1",
          chunk_id: "chunk-1",
          parent_chunk_id: "parent-1",
          title: "Alpha report",
          section_title: "Section A",
          source_type: "text",
          source_name: "alpha.txt",
          source_value: "alpha.txt",
          created_at: "2026-04-23T00:00:00Z",
          snippet: "Alpha snippet",
          context_snippet: "Alpha context",
          expanded_context_snippet: "Alpha expanded context"
        }
      ],
      used_grounded_items: [
        {
          snapshot_id: "snapshot-1",
          title: "Alpha report",
          final_category: "research",
          claim: "Alpha grounded claim",
          citation_ids: ["summary-cite-1"],
          evidence_titles: ["Alpha report"]
        }
      ],
      suggested_queries: []
    });

    const store = useQaStore();
    await store.answer({
      question: "What is alpha?",
      mode: "answer",
      filters: {
        source_types: ["text"],
        knowledge_item_ids: ["ki-1"],
        keyword: "alpha"
      }
    });

    expect(api.answerQuestion).toHaveBeenCalledWith({
      question: "What is alpha?",
      mode: "answer",
      filters: {
        source_types: ["text"],
        knowledge_item_ids: ["ki-1"],
        keyword: "alpha"
      }
    });
    expect(store.result?.answer).toBe("Alpha answer");
    expect(store.selectedSessionId).toBe("session-1");
    expect(store.result?.rewritten_question).toBe("What is alpha?");
    expect(store.error).toBeNull();
  });

  it("loads a persisted session detail and exposes its messages", async () => {
    vi.spyOn(api, "getQaSession").mockResolvedValue({
      session_id: "session-1",
      title: "What is alpha?",
      mode: "answer",
      created_at: "2026-04-24T00:00:00Z",
      updated_at: "2026-04-24T00:01:00Z",
      message_count: 2,
      messages: [
        {
          message_id: "msg-1",
          role: "user",
          content: "What is alpha?",
          created_at: "2026-04-24T00:00:00Z"
        },
        {
          message_id: "msg-2",
          role: "assistant",
          content: "Alpha answer",
          created_at: "2026-04-24T00:00:30Z",
          question: "What is alpha?",
          rewritten_question: "What is alpha?",
          rewrite: {
            rewritten_question: "What is alpha?",
            requires_history: false,
            used_history: false,
            intent: "answer",
            risk_flags: ["self_contained"],
            confidence: 0.72,
            strategy: "heuristic"
          },
          answer_status: "grounded",
          confidence: 0.8,
          citations: [],
          used_grounded_items: [],
          suggested_queries: [],
          applied_filters: {},
          verification: {
            status: "passed",
            reason: "citation_terms_support_answer",
            supported_terms: 3,
            answer_terms: 4
          },
          retry_count: 1
        }
      ]
    });

    const store = useQaStore();
    await store.loadSession("session-1");

    expect(store.selectedSessionId).toBe("session-1");
    expect(store.sessionDetail?.messages).toHaveLength(2);
    expect(store.sessionDetail?.messages[1]?.content).toBe("Alpha answer");
    expect(store.result?.answer).toBe("Alpha answer");
    expect(store.result?.session_id).toBe("session-1");
    expect(store.result?.rewrite.rewritten_question).toBe("What is alpha?");
    expect(store.result?.verification?.status).toBe("passed");
    expect(store.result?.retry_count).toBe(1);
  });

  it("tracks endpoint unavailable errors from the qa endpoint", async () => {
    vi.spyOn(api, "answerQuestion").mockRejectedValue(
      new ApiError(404, {
        error_category: "ENDPOINT_UNAVAILABLE",
        error_message: "qa endpoint unavailable"
      })
    );

    const store = useQaStore();
    await expect(
      store.answer({
        question: "What is alpha?",
        mode: "answer"
      })
    ).rejects.toBeInstanceOf(ApiError);

    expect(store.endpointUnavailable).toBe(true);
    expect(store.connectionUnavailable).toBe(false);
    expect(store.result).toBeNull();
  });
});
