import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import { usePoolStore } from "./pool";

describe("pool store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("reingests a pending item and keeps it pending locally", async () => {
    vi.spyOn(api, "getPoolItems").mockResolvedValue({
      items: [
        {
          id: "pool-1",
          knowledge_item_id: "ki-1",
          title: "Pending item",
          source_type: "url",
          source_value: "https://example.com/a",
          current_status: "pending",
          display_updated_at: "2026-04-22T00:00:00Z",
          is_deleted: false,
          was_resummarized: false
        }
      ],
      total: 1
    });
    vi.spyOn(api, "reingestPoolItem").mockResolvedValue({ accepted: true });

    const store = usePoolStore();
    await store.fetchItems();
    await store.reingestItem("pool-1");

    expect(api.reingestPoolItem).toHaveBeenCalledWith("pool-1");
    expect(store.items[0]).toMatchObject({
      id: "pool-1",
      current_status: "pending",
      was_resummarized: true
    });
  });
});
