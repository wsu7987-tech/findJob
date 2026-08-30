import { describe, expect, it } from "vitest";

import {
  canConfirmFineJobChatReply,
  fineJobChatConfirmBlocker,
  fineJobChatSendStatusLabel,
  shouldSyncFineJobChatEditor
} from "./fineJobChatPolicy";


const valid = {
  runtime: { send_enabled: true } as never,
  session: {
    status: "active",
    encrypt_peer_uid: "peer",
    security_id: "security",
    encrypt_job_id: "job"
  } as never,
  task: { status: "awaiting_review" } as never,
  finalText: "人工确认后的回复",
  leaderAvailable: true
};

describe("自动代聊页面安全策略", () => {
  it("只有发送权限、领导标签页、活动会话和有效草稿同时满足时才允许确认", () => {
    expect(canConfirmFineJobChatReply(valid)).toBe(true);
    expect(canConfirmFineJobChatReply({ ...valid, leaderAvailable: false })).toBe(false);
    expect(canConfirmFineJobChatReply({ ...valid, runtime: { send_enabled: false } as never })).toBe(false);
    expect(canConfirmFineJobChatReply({ ...valid, session: { status: "paused" } as never })).toBe(false);
    expect(canConfirmFineJobChatReply({ ...valid, task: { status: "stale" } as never })).toBe(false);
  });

  it("accepted 只展示为已提交发送", () => {
    expect(fineJobChatSendStatusLabel("accepted")).toBe("已提交发送");
    expect(fineJobChatSendStatusLabel("unknown")).toBe("结果未知");
  });

  it("返回可直接操作的确认阻塞原因", () => {
    expect(fineJobChatConfirmBlocker({ ...valid, leaderAvailable: false })).toContain("领导标签页");
    expect(fineJobChatConfirmBlocker({
      ...valid,
      session: {
        status: "active",
        encrypt_peer_uid: "",
        security_id: "security",
        encrypt_job_id: "job"
      } as never
    })).toContain("身份不完整");
    expect(fineJobChatConfirmBlocker({ ...valid, finalText: " " })).toContain("不能为空");
  });

  it("轮询同一任务时不要求重置人工编辑文本", () => {
    expect(shouldSyncFineJobChatEditor("task-1", "task-1")).toBe(false);
    expect(shouldSyncFineJobChatEditor("task-1", "task-2")).toBe(true);
  });
});
