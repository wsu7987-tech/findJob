import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  decodeObservedChatFrame,
  getBossChatDiagnostics,
  installBossChatObserver,
  markAssistantClientMid,
  resetBossChatDiagnostics
} from "../src/platform/boss/chat/observer";
import { bossChatProtocol } from "../src/platform/boss/chat/protocol";

const publish = (payload: Uint8Array): Uint8Array => Uint8Array.from([
  0x32, payload.byteLength + 8, 0, 4, 99, 104, 97, 116, 0, 7, ...payload
]);

describe("BOSS 聊天 observer 证据来源", () => {
  beforeEach(() => {
    (window as unknown as { _PAGE: Record<string, unknown> })._PAGE = { uid: "100", token: "test" };
    resetBossChatDiagnostics();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      json: async () => ({ zpData: { result: [{ encryptFriendId: "enc", securityId: "security", encryptJobId: "job" }] } })
    }));
  });

  it("local WebSocket.send 只生成 transport write evidence", async () => {
    markAssistantClientMid("1000000000000000001");
    const bytes = bossChatProtocol.encodeText({
      fromUid: "100", toUid: "200", encryptToUid: "enc", friendSource: 0,
      clientMid: "1000000000000000001", text: "测试内容"
    });

    const local = await decodeObservedChatFrame(publish(bytes), "local_send");
    const remote = await decodeObservedChatFrame(publish(bytes), "remote_message");

    expect(local[0]).toMatchObject({
      source: "assistant", frameOrigin: "local_send", evidenceSource: "local_transport_write"
    });
    expect(remote[0]).toMatchObject({
      source: "assistant", frameOrigin: "remote_message", evidenceSource: "remote_outbound_echo"
    });
  });

  it("解码 reference-only messageSync 并保留 clientMid/serverMid", async () => {
    const bytes = bossChatProtocol.encodeMessageSync("1000000000000000002", "2000000000000000002");
    const messages = await decodeObservedChatFrame(publish(bytes), "remote_message");
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      evidenceSource: "message_sync",
      clientMid: "1000000000000000002",
      serverMid: "2000000000000000002"
    });
    expect(getBossChatDiagnostics().messageSyncReceived).toBe(1);
  });

  it("畸形 MQTT 或 protobuf 只记录无内容诊断", async () => {
    await expect(decodeObservedChatFrame(Uint8Array.from([0x32, 0x80]), "remote_message")).resolves.toEqual([]);
    expect(getBossChatDiagnostics()).toMatchObject({ mqttFramesReceived: 1, protobufDecodeFailure: 1 });
  });

  it("重复安装复用同一 observer，uninstall 后恢复原生构造器", () => {
    const nativeWebSocket = window.WebSocket;
    class FakeWebSocket {
      url: string;
      constructor(url: string | URL) { this.url = String(url); }
      addEventListener = vi.fn();
      send(): void {}
    }
    window.WebSocket = FakeWebSocket as unknown as typeof WebSocket;
    const first = installBossChatObserver(async () => undefined);
    const second = installBossChatObserver(async () => undefined);
    expect(second).toBe(first);
    first.uninstall();
    expect(window.WebSocket).toBe(FakeWebSocket);
    window.WebSocket = nativeWebSocket;
  });
});
