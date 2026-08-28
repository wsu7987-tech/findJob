import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const deleteFineJobResumeMock = vi.hoisted(() => vi.fn());

vi.mock("@/services/api", () => ({
  ApiError: class ApiError extends Error {},
  NetworkError: class NetworkError extends Error {},
  api: {
    deleteFineJobResume: deleteFineJobResumeMock
  }
}));

import { useFineJobResumesStore } from "./fineJobResumes";

describe("fine job resumes store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    deleteFineJobResumeMock.mockReset().mockResolvedValue(undefined);
  });

  it("删除简历后同步移除列表、确认信息和当前选中项", async () => {
    const store = useFineJobResumesStore();
    store.resumes = [
      { id: "resume-1", name: "第一份简历" } as never,
      { id: "resume-2", name: "第二份简历" } as never
    ];
    store.selectedResume = store.resumes[0];
    store.facts = {
      "resume-1": [{ id: "fact-1" } as never]
    };

    await store.deleteResume("resume-1");

    expect(deleteFineJobResumeMock).toHaveBeenCalledWith("resume-1");
    expect(store.resumes.map((resume) => resume.id)).toEqual(["resume-2"]);
    expect(store.selectedResume?.id).toBe("resume-2");
    expect(store.facts["resume-1"]).toBeUndefined();
    expect(store.deleting).toBe(false);
  });
});
