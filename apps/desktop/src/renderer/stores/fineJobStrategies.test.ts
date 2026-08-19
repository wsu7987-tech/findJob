import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import { emptyFilterStrategy, useFineJobStrategiesStore } from "./fineJobStrategies";

describe("fineJobStrategies store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("loads filter and recommendation strategies together", async () => {
    vi.spyOn(api, "listFineJobFilterStrategies").mockResolvedValue({
      strategies: [{ ...emptyFilterStrategy(), id: "filter-1", name: "Agent 正职" }]
    });
    vi.spyOn(api, "listFineJobRecommendationStrategies").mockResolvedValue({
      strategies: []
    });

    const store = useFineJobStrategiesStore();
    await store.load();

    expect(store.filters).toHaveLength(1);
    expect(store.filters[0].name).toBe("Agent 正职");
  });

  it("creates a new filter strategy and refreshes lists", async () => {
    const payload = { ...emptyFilterStrategy(), name: "Agent 兼职" };
    vi.spyOn(api, "createFineJobFilterStrategy").mockResolvedValue({
      strategy: { ...payload, id: "filter-2" }
    });
    vi.spyOn(api, "listFineJobFilterStrategies").mockResolvedValue({
      strategies: [{ ...payload, id: "filter-2" }]
    });
    vi.spyOn(api, "listFineJobRecommendationStrategies").mockResolvedValue({
      strategies: []
    });

    const store = useFineJobStrategiesStore();
    const saved = await store.saveFilter(payload);

    expect(api.createFineJobFilterStrategy).toHaveBeenCalledWith(payload);
    expect(saved.id).toBe("filter-2");
    expect(store.filters[0].name).toBe("Agent 兼职");
  });
});
