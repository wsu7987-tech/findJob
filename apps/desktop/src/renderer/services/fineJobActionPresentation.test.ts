import { describe, expect, it } from "vitest";

import {
  fineJobActionReasonLabel,
  fineJobActionStatusLabel
} from "./fineJobActionPresentation";

describe("统一 Action 展示", () => {
  it("区分已发出、结果未知与已确认完成", () => {
    expect(fineJobActionStatusLabel("accepted")).toBe("已发出，等待平台确认");
    expect(fineJobActionStatusLabel("unknown")).toBe("结果无法确认");
    expect(fineJobActionStatusLabel("succeeded")).toBe("已确认完成");
  });

  it("优先展示后端已定义的阻止原因，未知原因保留原码", () => {
    expect(fineJobActionReasonLabel("classification_uncertain")).toContain("无法确认新消息");
    expect(fineJobActionReasonLabel("resume_attachment_invalid")).toContain("重新选择");
    expect(fineJobActionReasonLabel("custom_backend_reason")).toBe("custom_backend_reason");
  });
});
