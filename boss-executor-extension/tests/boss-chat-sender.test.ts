import { beforeEach, describe, expect, it, vi } from "vitest";

const mqttMock = vi.hoisted(() => {
  const client = {
    connected: true,
    once: vi.fn((event: string, callback: (error?: Error) => void) => {
      if (event === "connect") queueMicrotask(() => callback());
      return client;
    }),
    end: vi.fn(),
    publish: vi.fn((_topic: string, _bytes: Uint8Array, _options: object, callback: (error?: Error) => void) => callback())
  };
  return { client, connect: vi.fn(() => client) };
});
const resumeCardMock = vi.hoisted(() => ({
  wait: vi.fn()
}));

vi.mock("mqtt", () => ({ default: { connect: mqttMock.connect } }));
vi.mock("../src/platform/boss/chat/observer", () => ({
  markAssistantClientMid: vi.fn(),
  waitForResumeCard: resumeCardMock.wait
}));

import { BossChatSender, createProcessClientMid } from "../src/platform/boss/chat/sender";
import type { FineJobChatSendAction } from "../src/finejob/types";
import { bossChatProtocol } from "../src/platform/boss/chat/protocol";

const action = (overrides: Partial<FineJobChatSendAction> = {}): FineJobChatSendAction => ({
  id: "action-1",
  session_id: "session-1",
  status: "dispatching",
  text: "离线测试消息",
  execution_epoch: 1,
  account_uid: "100",
  peer_uid: "200",
  encrypt_peer_uid: "enc-200",
  security_id: "security-200",
  encrypt_job_id: "job-200",
  client_mid: "1000000000000000001",
  ...overrides
});

describe("BOSS 聊天 sender 离线边界", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resumeCardMock.wait.mockResolvedValue({ fromUid: "200", mid: "300000000000001" });
    (window as unknown as { _PAGE: Record<string, unknown> })._PAGE = { uid: "100", token: "test-token" };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ json: async () => ({ code: 0, zpData: { wt2: "test-wt" } }) }));
  });

  it("dry-run 不 fetch、不连接、也不 publish", async () => {
    const result = await new BossChatSender().send(action(), { dryRun: true });
    expect(result).toMatchObject({ outcome: "accepted", statusCode: "dry_run_prepared", clientMid: action().client_mid });
    if (process.env.FINEJOB_DRY_RUN_PRINT === "1") {
      console.info(`FINEJOB_DRY_RUN_NORMALIZED=${JSON.stringify(result.evidence.normalized)}`);
    }
    expect(fetch).not.toHaveBeenCalled();
    expect(mqttMock.connect).not.toHaveBeenCalled();
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it("简历 dry-run 不读取附件、不请求 exchange、不连接也不 publish", async () => {
    const result = await new BossChatSender().send(
      action({ operation_kind: "resume", encrypt_resume_id: "resume-1", resume_filename: "候选人.pdf" }),
      { dryRun: true }
    );
    expect(result).toMatchObject({ outcome: "accepted", statusCode: "dry_run_prepared" });
    expect(fetch).not.toHaveBeenCalled();
    expect(mqttMock.connect).not.toHaveBeenCalled();
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it("附件列表动作只读取列表，不连接 MQTT 或 publish", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      json: async () => ({ code: 0, zpData: { resumeList: [{ encryptResumeId: "resume-1", showName: "候选人.pdf" }] } })
    }));
    const result = await new BossChatSender().send(action({ operation_kind: "resume_list" }));
    expect(result).toMatchObject({ outcome: "accepted", statusCode: "resume_list_loaded" });
    expect(result.evidence).toMatchObject({ attachments: [{ encryptResumeId: "resume-1", showName: "候选人.pdf" }] });
    expect(mqttMock.connect).not.toHaveBeenCalled();
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it("零份附件只返回空列表，不连接 MQTT 或 publish", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      json: async () => ({ code: 0, zpData: { resumeList: [] } })
    }));
    const result = await new BossChatSender().send(action({ operation_kind: "resume_list" }));
    expect(result.evidence).toMatchObject({ attachments: [] });
    expect(mqttMock.connect).not.toHaveBeenCalled();
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it("简历先 exchange/request 再经既有 MQTT 管道发送", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ json: async () => ({ code: 0, zpData: { resumeList: [{ encryptResumeId: "resume-1", showName: "候选人.pdf" }] } }) })
      .mockResolvedValueOnce({ json: async () => ({ code: 0, zpData: { status: 0 } }) })
      .mockResolvedValueOnce({ json: async () => ({ code: 0, zpData: { wt2: "test-wt" } }) });
    vi.stubGlobal("fetch", fetchMock);
    const result = await new BossChatSender().send(
      action({ operation_kind: "resume", encrypt_resume_id: "resume-1", resume_filename: "候选人.pdf" }),
      { isSendEnabled: async () => true }
    );
    expect(result).toMatchObject({ outcome: "accepted", statusCode: "transport_accepted" });
    expect(fetchMock.mock.calls[0]?.[0]).toContain("resume/attachment/checkbox.json");
    expect(fetchMock.mock.calls[1]?.[0]).toBe("https://www.zhipin.com/wapi/zpchat/exchange/request");
    expect(String(fetchMock.mock.calls[1]?.[1]?.body)).toContain("encryptResumeId=resume-1");
    const publishCall = mqttMock.client.publish.mock.calls[0];
    expect(publishCall?.[0]).toBe("chat");
    expect((publishCall?.[1] as { length?: number }).length).toBeGreaterThan(0);
    expect(publishCall?.[2]).toEqual({ qos: 1, retain: true });
    const protocol = bossChatProtocol.decode(publishCall?.[1] as Uint8Array);
    expect(protocol.protocolType6Payload).toMatchObject({
      field1Value: "200",
      field2Value: "300000000000001",
      field5Value: 0
    });
    expect(Number(protocol.protocolType6Payload?.field3Value)).toBeGreaterThan(0);
    expect(resumeCardMock.wait).toHaveBeenCalledWith("200", 10_000, expect.any(AbortSignal));
  });

  it("exchange 失败时不 MQTT publish", async () => {
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce({ json: async () => ({ code: 0, zpData: { resumeList: [{ encryptResumeId: "resume-1", showName: "候选人.pdf" }] } }) })
      .mockResolvedValueOnce({ json: async () => ({ code: 1, message: "rejected", zpData: { status: 1 } }) })
    );
    const result = await new BossChatSender().send(
      action({ operation_kind: "resume", encrypt_resume_id: "resume-1", resume_filename: "候选人.pdf" }),
      { isSendEnabled: async () => true }
    );
    expect(result).toMatchObject({ outcome: "failed", statusCode: "chat_send_failed" });
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it("身份或发送开关不匹配时不 publish", async () => {
    const mismatch = await new BossChatSender().send(action({ account_uid: "other" }), { isSendEnabled: async () => true });
    const disabled = await new BossChatSender().send(action(), { isSendEnabled: async () => false });
    expect(mismatch.outcome).toBe("failed");
    expect(disabled.outcome).toBe("failed");
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it("简历发送开关关闭时不请求 exchange 或 MQTT", async () => {
    const result = await new BossChatSender().send(
      action({ operation_kind: "resume", encrypt_resume_id: "resume-1", resume_filename: "候选人.pdf" }),
      { isSendEnabled: async () => false }
    );
    expect(result).toMatchObject({ outcome: "failed", statusCode: "chat_send_failed" });
    expect(fetch).not.toHaveBeenCalled();
    expect(mqttMock.connect).not.toHaveBeenCalled();
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it("执行时附件不存在即阻止，不自动更换简历", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      json: async () => ({ code: 0, zpData: { resumeList: [{ encryptResumeId: "other", showName: "另一份.pdf" }] } })
    }));
    const result = await new BossChatSender().send(
      action({ operation_kind: "resume", encrypt_resume_id: "resume-1", resume_filename: "候选人.pdf" }),
      { isSendEnabled: async () => true }
    );
    expect(result).toMatchObject({ outcome: "failed", statusCode: "resume_attachment_invalid" });
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it.each(["session_id", "peer_uid", "encrypt_peer_uid", "security_id", "encrypt_job_id", "client_mid"] as const)(
    "缺少 %s 时不 publish",
    async (field) => {
      const result = await new BossChatSender().send(action({ [field]: "" }), { isSendEnabled: async () => true });
      expect(result.outcome).toBe("failed");
      expect(mqttMock.client.publish).not.toHaveBeenCalled();
    }
  );

  it("连接后关闭发送开关时最终 publish 边界 fail closed", async () => {
    const isSendEnabled = vi.fn().mockResolvedValueOnce(true).mockResolvedValueOnce(false);
    const result = await new BossChatSender().send(action(), { isSendEnabled });
    expect(result).toMatchObject({ outcome: "failed", statusCode: "chat_send_failed" });
    expect(isSendEnabled).toHaveBeenCalledTimes(2);
    expect(mqttMock.client.publish).not.toHaveBeenCalled();
  });

  it("QoS 1 callback 只表示 transport accepted", async () => {
    const result = await new BossChatSender().send(action(), { isSendEnabled: async () => true });
    expect(result).toMatchObject({ outcome: "accepted", statusCode: "transport_accepted" });
    expect(result.evidence).toMatchObject({
      transport_state: "transport_accepted_but_unconfirmed",
      platform_confirmed: false
    });
    expect(mqttMock.client.publish).toHaveBeenCalledTimes(1);
  });

  it("publish callback error 进入 unknown 且不进行业务级重试", async () => {
    mqttMock.client.publish.mockImplementationOnce(
      (_topic: string, _bytes: Uint8Array, _options: object, callback: (error?: Error) => void) => callback(new Error("offline"))
    );
    const result = await new BossChatSender().send(action(), { isSendEnabled: async () => true });
    expect(result).toMatchObject({ outcome: "unknown", statusCode: "chat_send_result_unknown" });
    expect(mqttMock.client.publish).toHaveBeenCalledTimes(1);
  });

  it("简历 exchange 已成功但 MQTT 失败时结果保持 unknown 且不重试", async () => {
    mqttMock.client.publish.mockImplementationOnce(
      (_topic: string, _bytes: Uint8Array, _options: object, callback: (error?: Error) => void) => callback(new Error("offline"))
    );
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ json: async () => ({ code: 0, zpData: { resumeList: [{ encryptResumeId: "resume-1", showName: "候选人.pdf" }] } }) })
      .mockResolvedValueOnce({ json: async () => ({ code: 0, zpData: { status: 0 } }) })
      .mockResolvedValueOnce({ json: async () => ({ code: 0, zpData: { wt2: "test-wt" } }) });
    vi.stubGlobal("fetch", fetchMock);
    const result = await new BossChatSender().send(
      action({ operation_kind: "resume", encrypt_resume_id: "resume-1", resume_filename: "候选人.pdf" }),
      { isSendEnabled: async () => true }
    );
    expect(result).toMatchObject({ outcome: "unknown", statusCode: "resume_send_result_unknown" });
    expect(mqttMock.client.publish).toHaveBeenCalledTimes(1);
  });

  it("进程内 clientMid 单调且一千次不重复", () => {
    const values = Array.from({ length: 1000 }, () => createProcessClientMid());
    expect(new Set(values)).toHaveLength(1000);
    expect(values.every((value, index) => index === 0 || BigInt(value) > BigInt(values[index - 1]!))).toBe(true);
  });
});
