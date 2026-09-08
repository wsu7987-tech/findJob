import { describe, expect, it, vi } from "vitest";

import { readResumeAttachmentSnapshot } from "../src/platform/boss/chat/resume-snapshot";

const command = {
  type: "BOSS_RESUME_SNAPSHOT_REQUEST" as const,
  targetTabId: "leader-tab",
  leaderEpoch: 1,
  requestId: "snapshot-request",
  actionId: "resume-action"
};

describe("简历附件只读 snapshot", () => {
  it("复用 sender 附件读取并只返回 Preflight 所需字段", async () => {
    const sender = {
      listResumeAttachments: vi.fn().mockResolvedValue([{
        encryptResumeId: "resume-1",
        showName: "候选人.pdf",
        resumeSizeDesc: "120KB",
        suffixName: "pdf"
      }])
    };

    const result = await readResumeAttachmentSnapshot(command, sender as never);

    expect(sender.listResumeAttachments).toHaveBeenCalledOnce();
    expect(result).toMatchObject({
      requestId: "snapshot-request",
      actionId: "resume-action",
      attachments: [{ encryptResumeId: "resume-1", filename: "候选人.pdf" }],
      error: ""
    });
    expect(result).not.toHaveProperty("outcome");
  });

  it("附件读取失败只回传 snapshot error", async () => {
    const sender = { listResumeAttachments: vi.fn().mockRejectedValue(new Error("附件读取失败")) };

    const result = await readResumeAttachmentSnapshot(command, sender as never);

    expect(result).toMatchObject({
      requestId: "snapshot-request",
      actionId: "resume-action",
      attachments: [],
      error: "附件读取失败"
    });
    expect(result).not.toHaveProperty("outcome");
  });
});
