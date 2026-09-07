import mqtt, { type MqttClient } from "mqtt";

import type { ChatSendExecutionResult, ChatSendOptions, FineJobChatSendAction } from "../../../finejob/types";
import { markAssistantClientMid } from "./observer";
import { bossChatProtocol } from "./protocol";


type PageIdentity = { uid: string; token: string };

let lastGeneratedClientMid = 0n;

/** 仅用于 dry-run 等没有服务端动作 ID 的场景，始终以字符串保留 int64 数值。 */
export const createProcessClientMid = (): string => {
  const candidate = BigInt(Date.now()) * 1000n;
  lastGeneratedClientMid = candidate > lastGeneratedClientMid ? candidate : lastGeneratedClientMid + 1n;
  return lastGeneratedClientMid.toString();
};

const readPageIdentity = (): PageIdentity => {
  const raw = (window as unknown as { _PAGE?: Record<string, unknown> })._PAGE ?? {};
  const uid = String(raw.uid ?? raw.userId ?? "");
  const token = String(raw.token ?? "");
  if (!uid || !token) throw new Error("未取得当前 BOSS 求职者登录信息");
  return { uid, token };
};

export class BossChatSender {
  private client: MqttClient | null = null;
  private connecting: Promise<MqttClient> | null = null;

  private async connect(): Promise<MqttClient> {
    if (this.client?.connected) return this.client;
    if (this.connecting) return this.connecting;
    if (this.client) {
      this.client.end(true);
      this.client = null;
    }
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
        reconnectPeriod: 0,
        connectTimeout: 10_000,
        protocolVersion: 4,
        createWebsocket: (url: string) => new WebSocket(url, wt ? [wt] : ["mqtt"])
      });
      this.client = client;
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
      client.once("close", () => {
        if (this.client === client) this.client = null;
      });
      return client;
    })().catch((error) => {
      this.client?.end(true);
      this.client = null;
      throw error;
    }).finally(() => {
      this.connecting = null;
    });
    return this.connecting;
  }

  async send(action: FineJobChatSendAction, options: ChatSendOptions = {}): Promise<ChatSendExecutionResult> {
    const clientMid = action.client_mid || (options.dryRun ? createProcessClientMid() : "");
    let publishStarted = false;
    try {
      const identity = readPageIdentity();
      if (identity.uid !== action.account_uid) {
        throw new Error("当前 BOSS 账号与待发送动作不一致");
      }
      if (!action.session_id || !action.text || !action.peer_uid || !action.encrypt_peer_uid || !action.security_id || !action.encrypt_job_id) {
        throw new Error("聊天对象身份不完整，已阻止发送");
      }
      if (!clientMid) throw new Error("发送动作缺少稳定 clientMid，已阻止发送");
      const bytes = bossChatProtocol.encodeText({
        fromUid: identity.uid,
        toUid: action.peer_uid,
        encryptToUid: action.encrypt_peer_uid,
        friendSource: 0,
        clientMid,
        text: action.text
      });
      const normalized = {
        transport: "mqtt",
        topic: "chat",
        qos: 1,
        retain: true,
        dup: false,
        techwolf: {
          protocolType: 1,
          messageCount: 1,
          messages: [{ fromUid: "<masked>", toUid: "<masked>", clientMid, bodyType: 1 }]
        }
      };
      if (options.dryRun) {
        return {
          actionId: action.id,
          executionEpoch: action.execution_epoch,
          outcome: "accepted",
          platformMessageId: "",
          clientMid,
          statusCode: "dry_run_prepared",
          message: "dry-run 已生成本地发送载荷，未执行网络副作用",
          evidence: { dry_run: true, normalized, payload_bytes: bytes.byteLength }
        };
      }
      const isSendEnabled = options.isSendEnabled ?? (async () => false);
      if (!await isSendEnabled()) throw new Error("自动代聊发送开关已关闭，已阻止发送");
      const client = await this.connect();
      // connect 期间用户可能关闭开关；publish 前必须重新读取当前授权。
      if (!await isSendEnabled()) throw new Error("自动代聊发送开关已关闭，已阻止发布");
      markAssistantClientMid(clientMid);
      publishStarted = true;
      await new Promise<void>((resolve, reject) => {
        const timeout = window.setTimeout(() => reject(new Error("MQTT 发送回执超时")), 10_000);
        client.publish(
          "chat",
          bytes as unknown as Parameters<MqttClient["publish"]>[1],
          { qos: 1, retain: true },
          (error) => {
            window.clearTimeout(timeout);
            if (error) reject(error);
            else resolve();
          }
        );
      });
      return {
        actionId: action.id,
        executionEpoch: action.execution_epoch,
        outcome: "accepted",
        platformMessageId: "",
        clientMid,
        statusCode: "transport_accepted",
        message: "MQTT QoS 1 已接受传输，等待平台确认",
        evidence: {
          transport_state: "transport_accepted_but_unconfirmed",
          platform_confirmed: false,
          normalized
        }
      };
    } catch (error) {
      return {
        actionId: action.id,
        executionEpoch: action.execution_epoch,
        outcome: publishStarted ? "unknown" : "failed",
        platformMessageId: "",
        clientMid,
        statusCode: publishStarted ? "chat_send_result_unknown" : "chat_send_failed",
        message: (error as Error).message || "BOSS 聊天发送失败",
        evidence: {}
      };
    }
  }
}

export const bossChatSender = new BossChatSender();
