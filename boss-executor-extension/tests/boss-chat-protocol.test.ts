import { beforeEach, describe, expect, it, vi } from "vitest";

import { decodeObservedChatFrame, markAssistantClientMid } from "../src/platform/boss/chat/observer";
import { bossChatProtocol } from "../src/platform/boss/chat/protocol";


const encodeLength = (length: number): number[] => {
  const bytes: number[] = [];
  let current = length;
  do {
    let next = current % 128;
    current = Math.floor(current / 128);
    if (current > 0) next |= 128;
    bytes.push(next);
  } while (current > 0);
  return bytes;
};

const mqttPublish = (payload: Uint8Array): Uint8Array => {
  const topic = new TextEncoder().encode("chat");
  const variable = Uint8Array.from([0, topic.length, ...topic, 0, 1]);
  return Uint8Array.from([
    0x33,
    ...encodeLength(variable.length + payload.length),
    ...variable,
    ...payload
  ]);
};

describe("BOSS 聊天协议", () => {
  beforeEach(() => {
    (window as unknown as { _PAGE: Record<string, unknown> })._PAGE = {
      uid: "100",
      token: "token"
    };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      json: async () => ({
        zpData: {
          result: [{
            uid: 200,
            encryptBossId: "enc-boss",
            securityId: "security",
            encryptJobId: "enc-job",
            brandName: "示例科技",
            title: "Python 开发",
            name: "王经理"
          }]
        }
      })
    }));
  });

  it("复用 MQTT chat + Techwolf Protobuf 解析人工文本消息", async () => {
    const protobuf = bossChatProtocol.encodeText({
      fromUid: "100",
      toUid: "200",
      encryptToUid: "enc-boss",
      friendSource: 0,
      clientMid: "123456",
      text: "您好，我人工回复一下"
    });
    const messages = await decodeObservedChatFrame(mqttPublish(protobuf));

    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      accountUid: "100",
      peerUid: "200",
      direction: "outbound",
      source: "manual",
      content: "您好，我人工回复一下",
      encryptPeerUid: "enc-boss",
      encryptJobId: "enc-job"
    });
  });

  it("忽略非 chat 主题和非 PUBLISH 数据", async () => {
    expect(await decodeObservedChatFrame(Uint8Array.from([0x20, 0]))).toEqual([]);
  });

  it("保留带 clientMid 的助手 outbound 平台回显", async () => {
    markAssistantClientMid("1000000000000000003");
    const protobuf = bossChatProtocol.encodeText({
      fromUid: "100",
      toUid: "200",
      encryptToUid: "enc-boss",
      friendSource: 0,
      clientMid: "1000000000000000003",
      text: "你好，我目前还在职"
    });

    const messages = await decodeObservedChatFrame(mqttPublish(protobuf));

    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      direction: "outbound",
      source: "assistant",
      clientMid: "1000000000000000003"
    });
  });

  it("按 Native Trace 编码 protocolType=6 简历通知的动态字段", () => {
    const protobuf = bossChatProtocol.encodeResume({
      field1Value: "200",
      field2Value: "300000000000001",
      field3Value: "1788748127339"
    });
    const decoded = bossChatProtocol.decode(protobuf) as Record<string, unknown>;
    expect(decoded.type).toBe(6);
    expect(decoded.protocolType6Payload).toMatchObject({
      field1Value: "200",
      field2Value: "300000000000001",
      field3Value: "1788748127339",
      field5Value: 0
    });
  });
});
