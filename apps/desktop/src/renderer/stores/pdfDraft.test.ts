import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import type { PdfDraft, PdfReparseJob } from "@/types";
import { usePdfDraftStore } from "./pdfDraft";

const buildDraft = (overrides: Partial<PdfDraft> = {}): PdfDraft => ({
  id: "draft-1",
  file_path: "D:/docs/demo.pdf",
  title: "Demo",
  source_name: "demo.pdf",
  created_at: "2026-04-18T00:00:00Z",
  updated_at: "2026-04-18T00:00:00Z",
  saved_parse_result_id: null,
  latest_preview_result_id: "parse-1",
  parse_results: [
    {
      id: "parse-1",
      parser_name: "pymupdf4llm_markdown",
      status: "running",
      raw_text: "",
      markdown_text: null,
      preview_text: "",
      page_count: 0,
      char_count: 0,
      quality_score: 0,
      is_ocr: false,
      warnings: [],
      created_at: "2026-04-18T00:00:00Z"
    }
  ],
  ...overrides
});

const buildJob = (overrides: Partial<PdfReparseJob> = {}): PdfReparseJob => ({
  id: "job-1",
  draft_id: "draft-1",
  parser_name: "auto",
  status: "running",
  created_at: "2026-04-18T00:00:00Z",
  processed_pages: 0,
  total_pages: 2,
  latest_available_page: 0,
  cancel_requested: false,
  ...overrides
});

describe("pdfDraft store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
    vi.useFakeTimers();
    vi.spyOn(api, "listPdfReparseJobs").mockResolvedValue({ jobs: [] });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("creates a draft and opens the drawer", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft(),
      job: buildJob()
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    expect(store.drawerOpen).toBe(true);
    expect(store.draft?.id).toBe("draft-1");
    expect(store.savedParseResult).toBeNull();
    expect(store.activeTaskCount).toBe(1);
  });

  it("keeps multiple drafts/jobs instead of overwriting the previous one", async () => {
    vi.spyOn(api, "createPdfDraft")
      .mockResolvedValueOnce({
        draft: buildDraft({
          id: "draft-1",
          file_path: "D:/docs/a.pdf",
          title: "A",
          source_name: "a.pdf",
          latest_preview_result_id: "parse-a",
          parse_results: [{ ...buildDraft().parse_results[0], id: "parse-a" }]
        }),
        job: buildJob({ id: "job-a", draft_id: "draft-1" })
      })
      .mockResolvedValueOnce({
        draft: buildDraft({
          id: "draft-2",
          file_path: "D:/docs/b.pdf",
          title: "B",
          source_name: "b.pdf",
          created_at: "2026-04-18T00:01:00Z",
          updated_at: "2026-04-18T00:01:00Z",
          latest_preview_result_id: "parse-b",
          parse_results: [{ ...buildDraft().parse_results[0], id: "parse-b" }]
        }),
        job: buildJob({
          id: "job-b",
          draft_id: "draft-2",
          created_at: "2026-04-18T00:01:00Z",
          total_pages: 3
        })
      });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/a.pdf", title: "A" });
    await store.createDraft({ file_path: "D:/docs/b.pdf", title: "B" });

    expect(store.activeTaskCount).toBe(2);
    expect(store.jobs.map((item) => item.id)).toEqual(["job-b", "job-a"]);
    expect(store.draftList.map((item) => item.id)).toEqual(["draft-1", "draft-2"]);
  });

  it("commits the draft and clears only that draft state", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [{ ...buildDraft().parse_results[0], status: "saved", raw_text: "alpha" }]
      }),
      job: buildJob({ status: "completed", processed_pages: 2, latest_available_page: 2 })
    });
    vi.spyOn(api, "commitPdfDraft").mockResolvedValue({
      item: {
        id: "pool-1",
        knowledge_item_id: "ki-1",
        source_type: "pdf",
        source_value: "D:/docs/demo.pdf",
        title: "Demo",
        current_status: "pending",
        display_updated_at: "2026-04-18T00:00:00Z",
        is_deleted: false,
        was_resummarized: false
      }
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    const item = await store.commitDraft();

    expect(item?.id).toBe("pool-1");
    expect(store.drawerOpen).toBe(false);
    expect(store.draft).toBeNull();
    expect(store.jobs).toEqual([]);
  });

  it("passes category and tags when committing a draft", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [{ ...buildDraft().parse_results[0], status: "saved", raw_text: "alpha" }]
      }),
      job: buildJob({ status: "completed", processed_pages: 2, latest_available_page: 2 })
    });
    vi.spyOn(api, "commitPdfDraft").mockResolvedValue({
      item: {
        id: "pool-1",
        knowledge_item_id: "ki-1",
        source_type: "pdf",
        source_value: "D:/docs/demo.pdf",
        title: "Demo",
        current_status: "pending",
        display_updated_at: "2026-04-18T00:00:00Z",
        is_deleted: false,
        was_resummarized: false
      }
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });
    await store.commitDraft({ category: "engineering", tags: ["pdf", "rag"] });

    expect(api.commitPdfDraft).toHaveBeenCalledWith("draft-1", {
      category: "engineering",
      tags: ["pdf", "rag"]
    });
  });

  it("passes cleaned text when committing a draft", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [{ ...buildDraft().parse_results[0], status: "saved", raw_text: "alpha" }]
      }),
      job: buildJob({ status: "completed", processed_pages: 2, latest_available_page: 2 })
    });
    vi.spyOn(api, "commitPdfDraft").mockResolvedValue({
      item: {
        id: "pool-1",
        knowledge_item_id: "ki-1",
        source_type: "pdf",
        source_value: "D:/docs/demo.pdf",
        title: "Demo",
        current_status: "pending",
        display_updated_at: "2026-04-18T00:00:00Z",
        is_deleted: false,
        was_resummarized: false
      }
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });
    await store.commitDraft({
      cleaned_text: "cleaned pdf body",
      cleaning_level: "enhanced"
    });

    expect(api.commitPdfDraft).toHaveBeenCalledWith("draft-1", {
      cleaned_text: "cleaned pdf body",
      cleaning_level: "enhanced"
    });
  });

  it("resets reparse loading when the request is aborted", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [{ ...buildDraft().parse_results[0], status: "saved", raw_text: "alpha" }]
      }),
      job: buildJob()
    });
    const abortError = new DOMException("aborted", "AbortError");
    vi.spyOn(api, "reparsePdfDraft").mockRejectedValue(abortError);

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    await expect(store.reparseDraft()).resolves.toBeNull();
    expect(store.reparsing).toBe(false);
    expect(store.error).toBeNull();
  });

  it("tracks reparse jobs and polls until completion", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            status: "saved",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            page_count: 1,
            char_count: 5,
            quality_score: 0.9
          }
        ]
      }),
      job: buildJob({ status: "completed", processed_pages: 1, total_pages: 1, latest_available_page: 1 })
    });
    vi.spyOn(api, "reparsePdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        latest_preview_result_id: "parse-2",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            id: "parse-1",
            status: "saved",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            page_count: 1,
            char_count: 5,
            quality_score: 0.9
          },
          {
            ...buildDraft().parse_results[0],
            id: "parse-2",
            parser_name: "rapid_ocr",
            status: "running",
            is_ocr: true,
            created_at: "2026-04-18T00:01:00Z"
          }
        ]
      }),
      job: buildJob({
        id: "job-1",
        parser_name: "rapid_ocr",
        status: "running",
        created_at: "2026-04-18T00:01:00Z",
        total_pages: 3
      })
    });
    vi.spyOn(api, "listPdfReparseJobs").mockResolvedValue({
      jobs: [
        buildJob({
          id: "job-1",
          parser_name: "rapid_ocr",
          status: "completed",
          created_at: "2026-04-18T00:01:00Z",
          processed_pages: 3,
          total_pages: 3,
          latest_available_page: 3,
          preview_result_id: "parse-2"
        })
      ]
    });
    vi.spyOn(api, "getPdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        latest_preview_result_id: "parse-2",
        updated_at: "2026-04-18T00:02:00Z",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            id: "parse-1",
            status: "saved",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            page_count: 1,
            char_count: 5,
            quality_score: 0.9
          },
          {
            ...buildDraft().parse_results[0],
            id: "parse-2",
            parser_name: "rapid_ocr",
            status: "preview",
            raw_text: "ocr page one",
            preview_text: "ocr page one",
            page_count: 3,
            char_count: 12,
            quality_score: 0.7,
            is_ocr: true,
            created_at: "2026-04-18T00:01:00Z"
          }
        ]
      })
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    await store.reparseDraft();
    expect(store.activeJob?.id).toBe("job-1");
    await vi.runOnlyPendingTimersAsync();

    expect(store.activeJob).toBeNull();
    expect(store.draft?.latest_preview_result_id).toBe("parse-2");
    expect(store.taskCards[0]?.status).toBe("ready");
  });

  it("loads preview pages on demand", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        latest_preview_result_id: "parse-2",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            id: "parse-2",
            parser_name: "pymupdf4llm_markdown",
            status: "preview",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            page_count: 3,
            char_count: 5,
            quality_score: 0.9
          }
        ]
      }),
      job: buildJob()
    });
    vi.spyOn(api, "getPdfDraftPreviewPage").mockResolvedValue({
      page: {
        page_number: 2,
        content_type: "markdown",
        content: "## Page 2"
      }
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    const page = await store.loadPreviewPage(2);

    expect(page?.page_number).toBe(2);
    expect(store.currentPageNumber).toBe(2);
    expect(store.activePreviewPage?.content).toBe("## Page 2");
  });

  it("uses the backend-resolved page number when preview pages are clamped server-side", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        latest_preview_result_id: "parse-2",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            id: "parse-2",
            parser_name: "pymupdf4llm_markdown",
            status: "preview",
            raw_text: "alpha",
            markdown_text: "# Alpha",
            preview_text: "# Alpha",
            page_count: 4,
            char_count: 5,
            quality_score: 0.9
          }
        ]
      }),
      job: buildJob()
    });
    vi.spyOn(api, "getPdfDraftPreviewPage").mockResolvedValue({
      page: {
        page_number: 3,
        content_type: "markdown",
        content: "## Page 3"
      }
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    const page = await store.loadPreviewPage(4);

    expect(page?.page_number).toBe(3);
    expect(store.currentPageNumber).toBe(3);
    expect(store.activePreviewPage?.content).toBe("## Page 3");
  });

  it("clamps preview page requests to the available page count", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        latest_preview_result_id: "parse-2",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            id: "parse-2",
            parser_name: "rapid_ocr",
            status: "preview",
            raw_text: "alpha",
            preview_text: "alpha",
            page_count: 3,
            char_count: 5,
            quality_score: 0.9,
            is_ocr: true
          }
        ]
      }),
      job: buildJob()
    });
    const getPreviewPage = vi.spyOn(api, "getPdfDraftPreviewPage").mockResolvedValue({
      page: {
        page_number: 3,
        content_type: "text",
        content: "page 3"
      }
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    const page = await store.loadPreviewPage(4);

    expect(getPreviewPage).toHaveBeenCalledWith("draft-1", "parse-2", 3);
    expect(page?.page_number).toBe(3);
    expect(store.currentPageNumber).toBe(3);
  });

  it("resets to the first preview page when opening another task", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        id: "draft-1",
        latest_preview_result_id: "parse-1",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            id: "parse-1",
            page_count: 5,
            status: "preview"
          }
        ]
      }),
      job: buildJob()
    });
    vi.spyOn(api, "getPdfDraft").mockResolvedValue({
      draft: buildDraft({
        id: "draft-2",
        file_path: "D:/docs/other.pdf",
        source_name: "other.pdf",
        latest_preview_result_id: "parse-2",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            id: "parse-2",
            parser_name: "rapid_ocr",
            status: "preview",
            page_count: 2,
            is_ocr: true
          }
        ]
      })
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });
    store.currentPageNumber = 4;

    await store.openTask("draft-2");

    expect(store.currentPageNumber).toBe(1);
    expect(store.activeDraftId).toBe("draft-2");
    expect(store.selectedParseResultId).toBe("parse-2");
  });

  it("cancels an in-flight parse and keeps the saved version ready", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            status: "saved",
            raw_text: "alpha"
          }
        ]
      }),
      job: buildJob()
    });
    vi.spyOn(api, "cancelPdfReparseJob").mockResolvedValue({
      job: buildJob({ cancel_requested: true, status: "cancelled" })
    });
    vi.spyOn(api, "getPdfDraft").mockResolvedValue({
      draft: buildDraft({
        saved_parse_result_id: "parse-1",
        latest_preview_result_id: "parse-2",
        parse_results: [
          {
            ...buildDraft().parse_results[0],
            id: "parse-1",
            status: "saved",
            raw_text: "alpha"
          },
          {
            ...buildDraft().parse_results[0],
            id: "parse-2",
            status: "cancelled",
            parser_name: "rapid_ocr"
          }
        ]
      })
    });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    await expect(store.cancelReparse()).resolves.toBe(true);
    expect(store.draft?.saved_parse_result_id).toBe("parse-1");
    expect(store.activeTaskCount).toBe(0);
    expect(store.taskCards[0]?.status).toBe("ready");
  });

  it("cancels an unsaved parse by deleting the draft", async () => {
    vi.spyOn(api, "createPdfDraft").mockResolvedValue({
      draft: buildDraft(),
      job: buildJob()
    });
    vi.spyOn(api, "cancelPdfReparseJob").mockResolvedValue({
      job: buildJob({ cancel_requested: true, status: "cancelled" })
    });
    vi.spyOn(api, "deletePdfDraft").mockResolvedValue({ deleted: true });

    const store = usePdfDraftStore();
    await store.createDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    await expect(store.cancelReparse()).resolves.toBe(true);
    expect(store.draft).toBeNull();
    expect(store.taskCards).toEqual([]);
  });
});
