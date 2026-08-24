import mqtt, { type MqttClient } from "mqtt";

import type { ChatSendExecutionResult, FineJobChatSendAction } from "../../../finejob/types";
import { markAssistantClientMid } from "./observer";
import { bossChatProtocol } from "./protocol";


type PageIdentity = { uid: string; token: string };

const readPageIdentity = (): PageIdentity => {
  const raw = (window as unknown as { _PAGE?: Record<string, unknown> })._PAGE ?? {};
  const uid = String(raw.uid ?? raw.userId ?? "");
  const token = String(raw.token ?? "");
  if (!uid || !token) throw new Error("未取得当前 BOSS 求职者登录信息");
  return { uid, token };
};

class BossChatSender {
  private client: MqttClient | null = null;
  private connecting: Promise<MqttClient> | null = null;

  private async connect(): Promise<MqttClient> {
    if (this.client?.connected) return this.client;
    if (this.connecting) return this.connecting;
    this.connecting = (async () => {
      const identity = readPageIdentity();
      const response = await fetch("https://www.zhipin.com/wapi/zppassport/get/wt", {
        credentials: "include"
      });
      const body = await response.json() as {
        code?: number;
        message?: string;
        zpData?: { wt2?: string };
      };
      const wt = body.zpData?.wt2 ?? "";
      if (body.code !== 0 || !wt) throw new Error(`获取 BOSS 聊天凭证失败：${body.message ?? "未知错误"}`);
      const client = mqtt.connect("wss://ws6.zhipin.com/chatws", {
        clientId: `ws-${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`,
        username: `${identity.token}|0`,
        password: wt,
        keepalive: 25,
        clean: true,
        reconnectPeriod: 1000,
        connectTimeout: 10_000,
        protocolVersion: 4,
        createWebsocket: (url: string) => new WebSocket(url, wt ? [wt] : ["mqtt"])
      });
      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => reject(new Error("BOSS 聊天连接超时")), 10_000);
        client.once("connect", () => {
          window.clearTimeout(timeout);
          resolve();
        });
        client.once("error", (error) => {
          window.clearTimeout(timeout);
          reject(error);
        });
      });
      this.client = client;
      return client;
    })().finally(() => {
      this.connecting = null;
    });
    return this.connecting;
  }

  async send(action: FineJobChatSendAction): Promise<ChatSendExecutionResult> {
    const clientMid = String(Date.now());
    try {
      const identity = readPageIdentity();
      if (identity.uid !== action.account_uid) {
        throw new Error("当前 BOSS 账号与待发送动作不一致");
      }
      const client = await this.connect();
      const bytes = bossChatProtocol.encodeText({
        fromUid: identity.uid,
        toUid: action.peer_uid,
        encryptToUid: action.encrypt_peer_uid,
        friendSource: 0,
        clientMid,
        text: action.text
      });
      markAssistantClientMid(clientMid);
      client.publish(
        "chat",
        bytes as unknown as Parameters<MqttClient["publish"]>[1],
        { qos: 1, retain: true }
      );
      return {
        actionId: action.id,
        executionEpoch: action.execution_epoch,
        outcome: "accepted",
        platformMessageId: "",
        clientMid,
        statusCode: "publish_no_throw",
        message: "MQTT publish 已提交发送",
        evidence: { topic: "chat", qos: 1, retain: true }
      };
    } catch (error) {
      return {
        actionId: action.id,
        executionEpoch: action.execution_epoch,
        outcome: "failed",
        platformMessageId: "",
        clientMid,
        statusCode: "chat_send_failed",
        message: (error as Error).message || "BOSS 聊天发送失败",
        evidence: {}
      };
    }
  }
}

export const bossChatSender = new BossChatSender();
