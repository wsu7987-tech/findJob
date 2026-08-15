import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

describe("pool api methods", () => {
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

  it("posts metadata suggestion requests to the backend", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          category: "engineering",
          tags: ["backend", "database"],
          strategy: "heuristic"
        }),
        {
          status: 200,
          headers: { "content-type": "application/json" }
        }
      )
    );
    vi.stubGlobal("fetch", fetchMock);

    const { api } = await import("./api");

    await api.suggestPoolMetadata({
      source_type: "text",
      source_value: "quick-capture",
      title: "Backend note",
      raw_text: "Database indexing and API workflow"
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:8000/api/pool/metadata-suggestions");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(
      JSON.stringify({
        source_type: "text",
        source_value: "quick-capture",
        title: "Backend note",
        raw_text: "Database indexing and API workflow"
      })
    );
  });
});
