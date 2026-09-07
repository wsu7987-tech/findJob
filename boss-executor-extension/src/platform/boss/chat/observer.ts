import type { ChatIdentity, ChatObservedMessage } from "../../../finejob/types";
import { bossChatProtocol, type DecodedChatMessage } from "./protocol";
import { decodeMqttPackets, toUint8Array } from "./mqtt-packet";
import { resolveBossContactContext } from "./contact-context";


const ASSISTANT_CLIENT_MIDS_KEY = "finejobBossChatAssistantClientMidsV1";
const ASSISTANT_CLIENT_MID_TTL_MS = 24 * 60 * 60_000;
const assistantClientMids = new Map<string, number>();
const observedSockets = new WeakSet<WebSocket>();
let activeInstallation: { setEnabled(enabled: boolean): void; uninstall(): void } | null = null;

export type ResumeCardReference = { fromUid: string; mid: string };
type ResumeCardWaiter = {
  peerUid: string;
  resolve: (reference: ResumeCardReference) => void;
  reject: (error: Error) => void;
  timer: number;
};
const resumeCardWaiters = new Set<ResumeCardWaiter>();

export type BossChatDiagnostics = {
  mqttFramesReceived: number;
  mqttPublishReceived: number;
  chatTopicReceived: number;
  protobufDecodeSuccess: number;
  protobufDecodeFailure: number;
  unknownProtocolType: number;
  unknownBodyType: number;
  messageSyncReceived: number;
};

const diagnostics: BossChatDiagnostics = {
  mqttFramesReceived: 0,
  mqttPublishReceived: 0,
  chatTopicReceived: 0,
  protobufDecodeSuccess: 0,
  protobufDecodeFailure: 0,
  unknownProtocolType: 0,
  unknownBodyType: 0,
  messageSyncReceived: 0
};

export const getBossChatDiagnostics = (): BossChatDiagnostics => ({ ...diagnostics });

export const resetBossChatDiagnostics = (): void => {
  for (const key of Object.keys(diagnostics) as Array<keyof BossChatDiagnostics>) diagnostics[key] = 0;
};

const pageRecord = (): Record<string, unknown> => {
  const page = (window as unknown as { _PAGE?: unknown })._PAGE;
  return page && typeof page === "object" ? page as Record<string, unknown> : {};
};

const currentAccountUid = (): string => {
  const page = pageRecord();
  return String(page.uid ?? page.userId ?? "");
};

const isoFromMilliseconds = (value: string | undefined): string => {
  const parsed = Number(value || 0);
  const date = parsed > 0 ? new Date(parsed) : new Date();
  return Number.isNaN(date.getTime()) ? new Date().toISOString() : date.toISOString();
};

export const readBossChatIdentity = (): ChatIdentity => ({
  accountUid: currentAccountUid(),
  loggedIn: Boolean(currentAccountUid() && pageRecord().token),
  pathname: window.location.pathname,
  observedAt: Date.now()
});

const notifyResumeCard = (reference: ResumeCardReference): void => {
  for (const waiter of [...resumeCardWaiters]) {
    if (waiter.peerUid !== reference.fromUid) continue;
    window.clearTimeout(waiter.timer);
    resumeCardWaiters.delete(waiter);
    waiter.resolve(reference);
  }
};

const findResumeCardReferences = async (data: unknown): Promise<ResumeCardReference[]> => {
  const bytes = await toUint8Array(data);
  if (!bytes) return [];
  try {
    return decodeMqttPackets(bytes).flatMap((packet) => {
      if (packet.type !== "publish" || packet.topic !== "chat") return [];
      const protocol = bossChatProtocol.decode(packet.payload);
      if (protocol.type !== 1) return [];
      return protocol.messages.flatMap((message) => {
        const fromUid = String(message.from?.uid ?? "");
        const mid = String(message.mid ?? "");
        return Number(message.body?.type) === 12 && fromUid && mid ? [{ fromUid, mid }] : [];
      });
    });
  } catch {
    return [];
  }
};

/** 等待 exchange/request 后页面收到的关联简历卡片，供 protocolType=6 使用其动态字段。 */
export const waitForResumeCard = (
  peerUid: string,
  timeoutMs = 10_000,
  signal?: AbortSignal
): Promise<ResumeCardReference> =>
  new Promise((resolve, reject) => {
    const waiter: ResumeCardWaiter = {
      peerUid,
      resolve,
      reject,
      timer: window.setTimeout(() => {
        resumeCardWaiters.delete(waiter);
        reject(new Error("未收到关联简历卡片，已阻止 MQTT 简历发布"));
      }, timeoutMs)
    };
    resumeCardWaiters.add(waiter);
    signal?.addEventListener("abort", () => {
      window.clearTimeout(waiter.timer);
      resumeCardWaiters.delete(waiter);
      reject(new Error("简历卡片等待已取消"));
    }, { once: true });
  });

const persistAssistantClientMids = (): void => {
  try {
    window.sessionStorage.setItem(
      ASSISTANT_CLIENT_MIDS_KEY,
      JSON.stringify(Object.fromEntries(assistantClientMids))
    );
  } catch {
    // 页面存储不可用时仍保留当前页面内存去重。
  }
};

const pruneAssistantClientMids = (): void => {
  const now = Date.now();
  for (const [clientMid, expiresAt] of assistantClientMids) {
    if (expiresAt <= now) assistantClientMids.delete(clientMid);
  }
};

try {
  const stored = JSON.parse(window.sessionStorage.getItem(ASSISTANT_CLIENT_MIDS_KEY) || "{}") as Record<string, number>;
  for (const [clientMid, expiresAt] of Object.entries(stored)) {
    if (expiresAt > Date.now()) assistantClientMids.set(clientMid, expiresAt);
  }
} catch {
  // 旧页面数据损坏时从空集合重新开始。
}

export const markAssistantClientMid = (clientMid: string): void => {
  pruneAssistantClientMids();
  assistantClientMids.set(clientMid, Date.now() + ASSISTANT_CLIENT_MID_TTL_MS);
  persistAssistantClientMids();
};

const normalizeMessage = async (
  message: DecodedChatMessage,
  frameOrigin: "local_send" | "remote_message"
): Promise<ChatObservedMessage | null> => {
  const accountUid = currentAccountUid();
  const senderUid = String(message.from?.uid ?? "");
  const receiverUid = String(message.to?.uid ?? "");
  if (!accountUid || !senderUid || !receiverUid) return null;
  const direction = senderUid === accountUid ? "outbound" : "inbound";
  const peerUid = direction === "inbound" ? senderUid : receiverUid;
  const clientMid = String(message.cmid ?? "");
  pruneAssistantClientMids();
  const assistantObserved = direction === "outbound" && assistantClientMids.has(clientMid);
  // 本机 write 只是传输尝试，保留 clientMid 等待远端回显或 messageSync 再消费标记。
  if (assistantObserved && frameOrigin === "remote_message") {
    assistantClientMids.delete(clientMid);
    persistAssistantClientMids();
  }
  const platformMessageId = String(
    message.mid ?? message.cmid ?? `${senderUid}:${message.time ?? Date.now()}:${message.body?.type ?? 0}`
  );
  const messageEncryptJobId = String(message.bizId ?? "");
  const contact = await resolveBossContactContext(accountUid, peerUid, messageEncryptJobId);
  const bodyType = Number(message.body?.type ?? 0);
  const messageType = bodyType === 1 ? "text" : bodyType === 3 ? "image" : "unknown";
  if (messageType === "unknown") diagnostics.unknownBodyType += 1;
  const evidenceSource = frameOrigin === "local_send"
    ? "local_transport_write"
    : direction === "outbound" ? "remote_outbound_echo" : "remote_message";
  return {
    eventId: `${accountUid}:${direction}:${platformMessageId}`,
    accountUid,
    platformMessageId,
    direction,
    messageType,
    content: String(message.body?.text ?? ""),
    senderUid,
    receiverUid,
    clientMid,
    peerUid,
    encryptPeerUid: contact.encryptPeerUid,
    securityId: contact.securityId || String(message.securityId ?? ""),
    encryptJobId: contact.encryptJobId || messageEncryptJobId,
    jobTitle: contact.jobTitle,
    peerName: contact.peerName || String(
      direction === "inbound" ? message.from?.name ?? "" : message.to?.name ?? ""
    ),
    companyName: contact.companyName || String(message.from?.company ?? message.to?.company ?? ""),
    sentAt: isoFromMilliseconds(message.time),
    observedAt: new Date().toISOString(),
    source: direction === "outbound" ? (assistantObserved ? "assistant" : "manual") : "websocket",
    frameOrigin,
    evidenceSource,
    serverMid: "",
    rawMeta: { bodyType, messageType: message.type ?? 0 }
  };
};

const normalizeMessageSync = (
  clientMid: string,
  serverMid: string,
  frameOrigin: "local_send" | "remote_message"
): ChatObservedMessage | null => {
  const accountUid = currentAccountUid();
  if (!accountUid || !clientMid || !serverMid || frameOrigin !== "remote_message") return null;
  diagnostics.messageSyncReceived += 1;
  return {
    eventId: `${accountUid}:message-sync:${clientMid}:${serverMid}`,
    accountUid,
    platformMessageId: serverMid,
    direction: "outbound",
    messageType: "system",
    content: "",
    senderUid: accountUid,
    receiverUid: "",
    clientMid,
    peerUid: "",
    encryptPeerUid: "",
    securityId: "",
    encryptJobId: "",
    jobTitle: "",
    peerName: "",
    companyName: "",
    sentAt: new Date().toISOString(),
    observedAt: new Date().toISOString(),
    source: "websocket",
    frameOrigin,
    evidenceSource: "message_sync",
    serverMid,
    rawMeta: { protocolEvidence: "reference_only" }
  };
};

export const decodeObservedChatFrame = async (
  data: unknown,
  frameOrigin: "local_send" | "remote_message" = "remote_message"
): Promise<ChatObservedMessage[]> => {
  const bytes = await toUint8Array(data);
  if (!bytes) return [];
  diagnostics.mqttFramesReceived += 1;
  try {
    const packets = decodeMqttPackets(bytes);
    const publishes = packets.filter((packet) => packet.type === "publish");
    diagnostics.mqttPublishReceived += publishes.length;
    const messages: ChatObservedMessage[] = [];
    for (const publish of publishes) {
      if (publish.topic !== "chat") continue;
      diagnostics.chatTopicReceived += 1;
      const protocol = bossChatProtocol.decode(publish.payload);
      diagnostics.protobufDecodeSuccess += 1;
      if (protocol.type !== 1 && protocol.type !== 5) diagnostics.unknownProtocolType += 1;
      const normalized = await Promise.all(protocol.messages.map((message) => normalizeMessage(message, frameOrigin)));
      messages.push(...normalized.filter((item): item is ChatObservedMessage => item !== null));
      for (const sync of protocol.messageSync) {
        const normalizedSync = normalizeMessageSync(String(sync.clientMid ?? ""), String(sync.serverMid ?? ""), frameOrigin);
        if (normalizedSync) messages.push(normalizedSync);
      }
    }
    return messages;
  } catch {
    diagnostics.protobufDecodeFailure += 1;
    return [];
  }
};

const observeSocket = (
  socket: WebSocket,
  onMessage: (message: ChatObservedMessage) => Promise<void>,
  isEnabled: () => boolean
): void => {
  if (observedSockets.has(socket) || !socket.url.includes("chat")) return;
  observedSockets.add(socket);
  socket.addEventListener("message", (event) => {
    void findResumeCardReferences(event.data).then((references) => {
      references.forEach(notifyResumeCard);
    });
    if (!isEnabled()) return;
    void decodeObservedChatFrame(event.data, "remote_message").then((messages) => Promise.all(
      messages.map((message) => onMessage(message))
    ));
  });
};

export const installBossChatObserver = (
  onMessage: (message: ChatObservedMessage) => Promise<void>
): { setEnabled(enabled: boolean): void; uninstall(): void } => {
  if (activeInstallation) return activeInstallation;
  const NativeWebSocket = window.WebSocket;
  const nativeSend = NativeWebSocket.prototype.send;
  let enabled = false;

  class ObservedWebSocket extends NativeWebSocket {
    constructor(url: string | URL, protocols?: string | string[]) {
      super(url, protocols ?? []);
      observeSocket(this, onMessage, () => enabled);
    }
  }

  NativeWebSocket.prototype.send = function (data: string | ArrayBufferLike | Blob | ArrayBufferView) {
    observeSocket(this, onMessage, () => enabled);
    if (enabled && this.url.includes("chat")) {
      void decodeObservedChatFrame(data, "local_send").then((messages) => Promise.all(
        messages.map((message) => onMessage(message))
      ));
    }
    return nativeSend.call(this, data);
  };
  window.WebSocket = ObservedWebSocket;

  const installation = {
    setEnabled(value: boolean) { enabled = value; },
    uninstall() {
      window.WebSocket = NativeWebSocket;
      NativeWebSocket.prototype.send = nativeSend;
      if (activeInstallation === installation) activeInstallation = null;
    }
  };
  activeInstallation = installation;
  return installation;
};
