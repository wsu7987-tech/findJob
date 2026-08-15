import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("pdf draft api methods", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.stubGlobal("window", {
      desktopBridge: {
        getMeta: vi.fn().mockResolvedValue({
          backendOrigin: "http://127.0.0.1:8000",
          isElectron: true,
          version: "test"
        })
      }
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("posts create draft payload to the backend", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ draft: { id: "draft-1", parse_results: [] } }), {
        status: 201,
        headers: { "content-type": "application/json" }
      })
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("./api");

    await api.createPdfDraft({ file_path: "D:/docs/demo.pdf", title: "Demo" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/pdf/drafts");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ file_path: "D:/docs/demo.pdf", title: "Demo" }));
  });

  it("posts reparse and commit requests to the backend", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          draft: { id: "draft-1", parse_results: [] },
          job: {
            id: "job-1",
            draft_id: "draft-1",
            parser_name: "rapid_ocr",
            status: "queued",
            created_at: "2026-04-19T00:00:00Z",
            processed_pages: 0,
            total_pages: 0,
            latest_available_page: 0,
            cancel_requested: false
          }
        }), {
          status: 202,
          headers: { "content-type": "application/json" }
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ item: { id: "pool-1", source_type: "pdf" } }), {
          status: 201,
          headers: { "content-type": "application/json" }
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("./api");

    await api.reparsePdfDraft("draft-1", { parser_name: "rapid_ocr" });
    await api.commitPdfDraft("draft-1");

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("http://127.0.0.1:8000/api/pdf/drafts/draft-1/reparse");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("http://127.0.0.1:8000/api/pdf/drafts/draft-1/commit");
  });

  it("queries job status, preview page, and job cancellation endpoints", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          draft: { id: "draft-1", parse_results: [] },
          job: {
            id: "job-1",
            draft_id: "draft-1",
            parser_name: "rapid_ocr",
            status: "queued",
            created_at: "2026-04-19T00:00:00Z",
            processed_pages: 0,
            total_pages: 0,
            latest_available_page: 0,
            cancel_requested: false
          }
        }), {
          status: 202,
          headers: { "content-type": "application/json" }
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          job: {
            id: "job-1",
            draft_id: "draft-1",
            parser_name: "rapid_ocr",
            status: "running",
            created_at: "2026-04-19T00:00:00Z",
            processed_pages: 1,
            total_pages: 4,
            latest_available_page: 1,
            cancel_requested: false
          }
        }), {
          status: 200,
          headers: { "content-type": "application/json" }
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          page: {
            page_number: 1,
            content_type: "text",
            content: "page-1"
          }
        }), {
          status: 200,
          headers: { "content-type": "application/json" }
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({
          job: {
            id: "job-1",
            draft_id: "draft-1",
            parser_name: "rapid_ocr",
            status: "running",
            created_at: "2026-04-19T00:00:00Z",
            processed_pages: 1,
            total_pages: 4,
            latest_available_page: 1,
            cancel_requested: true
          }
        }), {
          status: 202,
          headers: { "content-type": "application/json" }
        })
      );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("./api");
    const controller = new AbortController();

    await api.reparsePdfDraft("draft-1", { parser_name: "rapid_ocr" }, controller.signal);
    await api.getPdfReparseJob("draft-1", "job-1");
    await api.getPdfDraftPreviewPage("draft-1", "parse-1", 1);
    await api.cancelPdfReparseJob("draft-1", "job-1");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.signal).toBe(controller.signal);
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      "http://127.0.0.1:8000/api/pdf/drafts/draft-1/jobs/job-1"
    );
    expect(fetchMock.mock.calls[2]?.[0]).toBe(
      "http://127.0.0.1:8000/api/pdf/drafts/draft-1/parse-results/parse-1/pages/1"
    );
    expect(fetchMock.mock.calls[3]?.[0]).toBe(
      "http://127.0.0.1:8000/api/pdf/drafts/draft-1/jobs/job-1/cancel"
    );
  });
});
