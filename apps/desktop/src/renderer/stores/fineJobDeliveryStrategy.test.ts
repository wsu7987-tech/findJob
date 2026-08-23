import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

import { api } from "@/services/api";
import {
  emptyDeliveryStrategy,
  useFineJobDeliveryStrategyStore
} from "./fineJobDeliveryStrategy";

describe("fineJobDeliveryStrategy store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("强制刷新验证默认关闭并能随投递策略保存", async () => {
    const payload = {
      ...emptyDeliveryStrategy(),
      force_contact_verification_enabled: true
    };
    vi.spyOn(api, "saveFineJobDeliveryStrategy").mockResolvedValue({ strategy: payload });
    const store = useFineJobDeliveryStrategyStore();

    const saved = await store.save(payload);

    expect(emptyDeliveryStrategy().force_contact_verification_enabled).toBe(false);
    expect(saved?.force_contact_verification_enabled).toBe(true);
    expect(api.saveFineJobDeliveryStrategy).toHaveBeenCalledWith(payload);
  });
});
