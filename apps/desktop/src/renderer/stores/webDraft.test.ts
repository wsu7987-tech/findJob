import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import type { WebDraft, WebReparseJob } from "@/types";
import { useWebDraftStore } from "./webDraft";

const buildWebDraft = (overrides: Partial<WebDraft> = {}): WebDraft => ({
  id: "web-draft-1",
  url: "https://example.com/a",
  title: "Example",
  source_name: "example.com",
  session_profile_id: null,
  created_at: "2026-04-20T00:00:00Z",
  updated_at: "2026-04-20T00:00:00Z",
  saved_parse_result_id: null,
  latest_preview_result_id: "parse-1",
  parse_results: [
    {
      id: "parse-1",
      parser_name: "playwright_dom",
      status: "running",
      raw_text: "",
      markdown_text: null,
      preview_text: "",
      section_count: 0,
      char_count: 0,
      quality_score: 0,
      warnings: [],
      auth_mode: "none",
      created_at: "2026-04-20T00:00:00Z"
    }
  ],
  ...overrides
});

const buildWebJob = (overrides: Partial<WebReparseJob> = {}): WebReparseJob => ({
  id: "web-job-1",
  draft_id: "web-draft-1",
  parser_name: "playwright_dom",
  status: "running",
  created_at: "2026-04-20T00:00:00Z",
  processed_pages: 0,
  total_pages: 2,
  latest_available_page: 0,
  cancel_requested: false,
  ...overrides
});

describe("webDraft store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
    vi.useFakeTimers();
    vi.spyOn(api, "listWebReparseJobs").mockResolvedValue({ jobs: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("creates a draft and opens the drawer", async () => {
    vi.spyOn(api, "createWebDraft").mockResolvedValue({
      draft: buildWebDraft(),
      job: buildWebJob()
    });

    const store = useWebDraftStore();
    await store.createDraft({ url: "https://example.com/a", title: "Example", session_profile_id: null });

    expect(store.drawerOpen).toBe(true);
    expect(store.draft?.id).toBe("web-draft-1");
    expect(store.activeTaskCount).toBe(1);
  });

  it("renders a ready task card after polling completes", async () => {
    vi.spyOn(api, "createWebDraft").mockResolvedValue({
      draft: buildWebDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [
          {
            ...buildWebDraft().parse_results[0],
            status: "saved",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            section_count: 1,
            char_count: 5,
            quality_score: 0.9,
            auth_mode: "browser_profile"
          }
        ]
      }),
      job: buildWebJob({ status: "completed", processed_pages: 1, total_pages: 1, latest_available_page: 1 })
    });
    vi.spyOn(api, "getWebDraft").mockResolvedValue({
      draft: buildWebDraft({
        saved_parse_result_id: "parse-1",
        latest_preview_result_id: "parse-1",
        updated_at: "2026-04-20T00:01:00Z",
        parse_results: [
          {
            ...buildWebDraft().parse_results[0],
            status: "saved",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            section_count: 1,
            char_count: 5,
            quality_score: 0.9,
            auth_mode: "browser_profile"
          }
        ]
      })
    });
    vi.spyOn(api, "listWebReparseJobs").mockResolvedValue({
      jobs: [
        buildWebJob({
          status: "completed",
          processed_pages: 1,
          total_pages: 1,
          latest_available_page: 1,
          preview_result_id: "parse-1"
        })
      ]
    });

    const store = useWebDraftStore();
    await store.createDraft({ url: "https://example.com/a", title: "Example", session_profile_id: null });
    await vi.runOnlyPendingTimersAsync();

    expect(store.taskCards[0]?.status).toBe("ready");
  });

  it("renders a failed task card with the failure message", async () => {
    vi.spyOn(api, "createWebDraft").mockResolvedValue({
      draft: buildWebDraft({
        parse_results: [
          {
            ...buildWebDraft().parse_results[0],
            status: "failed",
            warnings: ["Playwright runtime is unavailable."]
          }
        ]
      }),
      job: buildWebJob({ status: "failed", error_message: "Playwright runtime is unavailable." })
    });

    const store = useWebDraftStore();
    await store.createDraft({
      url: "https://example.com/a",
      title: "Example",
      session_profile_id: null
    });

    expect(store.taskCards[0]?.status).toBe("failed");
    expect(store.taskCards[0]?.progressLabel).toBe("Playwright runtime is unavailable.");
  });

  it("reuses the draft session profile when reparsing", async () => {
    vi.spyOn(api, "createWebDraft").mockResolvedValue({
      draft: buildWebDraft({ session_profile_id: "session-1" }),
      job: buildWebJob()
    });
    vi.spyOn(api, "reparseWebDraft").mockResolvedValue({
      draft: buildWebDraft({ session_profile_id: "session-1" }),
      job: buildWebJob({ id: "web-job-2" })
    });

    const store = useWebDraftStore();
    await store.createDraft({ url: "https://example.com/a", title: "Example", session_profile_id: "session-1" });
    await store.reparseDraft();

    expect(api.reparseWebDraft).toHaveBeenCalledWith(
      "web-draft-1",
      {
        parser_name: "playwright_dom",
        session_profile_id: "session-1"
      },
      expect.any(AbortSignal)
    );
  });

  it("passes category and tags when committing a web draft", async () => {
    vi.spyOn(api, "createWebDraft").mockResolvedValue({
      draft: buildWebDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [
          {
            ...buildWebDraft().parse_results[0],
            status: "saved",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            section_count: 1,
            char_count: 5,
            quality_score: 0.9,
            auth_mode: "browser_profile"
          }
        ]
      }),
      job: buildWebJob({ status: "completed", processed_pages: 1, total_pages: 1, latest_available_page: 1 })
    });
    vi.spyOn(api, "commitWebDraft").mockResolvedValue({
      item: {
        id: "pool-1",
        knowledge_item_id: "ki-1",
        source_type: "url",
        source_value: "https://example.com/a",
        title: "Example",
        current_status: "pending",
        display_updated_at: "2026-04-20T00:00:00Z",
        is_deleted: false,
        was_resummarized: false
      }
    });

    const store = useWebDraftStore();
    await store.createDraft({
      url: "https://example.com/a",
      title: "Example",
      session_profile_id: null
    });
    await store.commitDraft({ category: "research", tags: ["url", "summary"] });

    expect(api.commitWebDraft).toHaveBeenCalledWith("web-draft-1", {
      category: "research",
      tags: ["url", "summary"]
    });
  });

  it("passes cleaned text when committing a web draft", async () => {
    vi.spyOn(api, "createWebDraft").mockResolvedValue({
      draft: buildWebDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [
          {
            ...buildWebDraft().parse_results[0],
            status: "saved",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            section_count: 1,
            char_count: 5,
            quality_score: 0.9,
            auth_mode: "browser_profile"
          }
        ]
      }),
      job: buildWebJob({ status: "completed", processed_pages: 1, total_pages: 1, latest_available_page: 1 })
    });
    vi.spyOn(api, "commitWebDraft").mockResolvedValue({
      item: {
        id: "pool-1",
        knowledge_item_id: "ki-1",
        source_type: "url",
        source_value: "https://example.com/a",
        title: "Example",
        current_status: "pending",
        display_updated_at: "2026-04-20T00:00:00Z",
        is_deleted: false,
        was_resummarized: false
      }
    });

    const store = useWebDraftStore();
    await store.createDraft({
      url: "https://example.com/a",
      title: "Example",
      session_profile_id: null
    });
    await store.commitDraft({
      cleaned_text: "cleaned web body",
      cleaning_level: "enhanced"
    });

    expect(api.commitWebDraft).toHaveBeenCalledWith("web-draft-1", {
      cleaned_text: "cleaned web body",
      cleaning_level: "enhanced"
    });
  });
});
