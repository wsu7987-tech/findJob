import { describe, expect, it } from "vitest";

import { resolveFineJobResumeSelection } from "./fineJobResumeSelection";

const attachment = (id: string) => ({
  encryptResumeId: id,
  showName: `${id}.pdf`,
  resumeSizeDesc: "",
  suffixName: "pdf"
});

describe("FineJob 附件简历选择", () => {
  it("零份附件保持为空并禁止形成选择", () => {
    expect(resolveFineJobResumeSelection([], "old")).toBe("");
  });

  it("唯一附件自动选中", () => {
    expect(resolveFineJobResumeSelection([attachment("only")], "")).toBe("only");
  });

  it("多份附件不默认选择第一份", () => {
    expect(resolveFineJobResumeSelection([attachment("first"), attachment("second")], "")).toBe("");
  });

  it("多份附件保留用户明确选择", () => {
    expect(resolveFineJobResumeSelection([attachment("first"), attachment("second")], "second")).toBe("second");
  });
});
