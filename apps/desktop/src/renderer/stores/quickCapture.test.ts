import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import { useQuickCaptureStore } from "./quickCapture";

describe("quickCapture store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("commits title, category, tags, and captured time into the pool payload", async () => {
    vi.spyOn(api, "createPoolItem").mockResolvedValue({
      item: {
        id: "pool-1",
        source_type: "text"
      }
    } as never);

    const store = useQuickCaptureStore();
    store.title = "Inbox note";
    store.category = "Research";
    store.tags = ["ocr", "competitor"];
    store.body = "Body text";
    store.capturedAt = "2026-04-21T10:30:00+08:00";
    store.captureSource = "screenshot_ocr";

    await store.commit();

    expect(api.createPoolItem).toHaveBeenCalledWith(
      expect.objectContaining({
        source_type: "text",
        title: "Inbox note",
        category: "Research",
        tags: ["ocr", "competitor"],
        captured_at: "2026-04-21T10:30:00+08:00",
        capture_source: "screenshot_ocr"
      })
    );
  });

  it("marks OCR as ready after image text is applied", () => {
    const store = useQuickCaptureStore();

    store.applyOcrResult({
      raw_text: "OCR body",
      captured_at: "2026-04-21T10:30:00+08:00",
      warnings: []
    });

    expect(store.body).toBe("OCR body");
    expect(store.captureSource).toBe("screenshot_ocr");
    expect(store.ocrStatusText).toBe("OCR 已就绪");
  });

  it("applies AI metadata suggestions to category and tags", async () => {
    vi.spyOn(api, "suggestPoolMetadata").mockResolvedValue({
      category: "engineering",
      tags: ["backend", "database"],
      strategy: "heuristic"
    });

    const store = useQuickCaptureStore();
    store.title = "Backend note";
    store.body = "Database indexing and API workflow";

    await store.suggestMetadata();

    expect(api.suggestPoolMetadata).toHaveBeenCalledWith({
      source_type: "text",
      source_value: "quick-capture",
      title: "Backend note",
      raw_text: "Database indexing and API workflow"
    });
    expect(store.category).toBe("engineering");
    expect(store.tags).toEqual(["backend", "database"]);
  });

  it("does not turn metadata suggestion failures into OCR failures", async () => {
    vi.spyOn(api, "suggestPoolMetadata").mockRejectedValue(new Error("metadata failed"));

    const store = useQuickCaptureStore();
    store.applyOcrResult({
      raw_text: "OCR body",
      captured_at: "2026-04-21T10:30:00+08:00",
      warnings: []
    });

    await expect(store.suggestMetadata()).rejects.toThrow("metadata failed");
    expect(store.ocrStatusText).toBe("OCR 已就绪");
  });
  it("can apply enhanced cleaning and restore the original body", () => {
    const store = useQuickCaptureStore();
    store.body = "导航\n第一行没有句号\n第二行继续";

    store.applyEnhancedCleaning();

    expect(store.body).toBe("第一行没有句号第二行继续");

    store.restorePreCleaningBody();

    expect(store.body).toBe("导航\n第一行没有句号\n第二行继续");
  });
});
