import type { ChatIdentity, ChatObservedMessage } from "../../../finejob/types";
import { bossChatProtocol, type DecodedChatMessage } from "./protocol";
import { decodeMqttPublish, toUint8Array } from "./mqtt-packet";
import { resolveBossContactContext } from "./contact-context";


const ASSISTANT_CLIENT_MIDS_KEY = "finejobBossChatAssistantClientMidsV1";
const ASSISTANT_CLIENT_MID_TTL_MS = 24 * 60 * 60_000;
const assistantClientMids = new Map<string, number>();
const observedSockets = new WeakSet<WebSocket>();

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

const normalizeMessage = async (message: DecodedChatMessage): Promise<ChatObservedMessage | null> => {
  const accountUid = currentAccountUid();
  const senderUid = String(message.from?.uid ?? "");
  const receiverUid = String(message.to?.uid ?? "");
  if (!accountUid || !senderUid || !receiverUid) return null;
  const direction = senderUid === accountUid ? "outbound" : "inbound";
  const peerUid = direction === "inbound" ? senderUid : receiverUid;
  const clientMid = String(message.cmid ?? "");
  pruneAssistantClientMids();
  if (direction === "outbound" && assistantClientMids.has(clientMid)) return null;
  const platformMessageId = String(
    message.mid ?? message.cmid ?? `${senderUid}:${message.time ?? Date.now()}:${message.body?.type ?? 0}`
  );
  const messageEncryptJobId = String(message.bizId ?? "");
  const contact = await resolveBossContactContext(accountUid, peerUid, messageEncryptJobId);
  const bodyType = Number(message.body?.type ?? 0);
  const messageType = bodyType === 1 ? "text" : bodyType === 3 ? "image" : "system";
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
    source: direction === "outbound" ? "manual" : "websocket",
    rawMeta: { bodyType, messageType: message.type ?? 0 }
  };
};

export const decodeObservedChatFrame = async (data: unknown): Promise<ChatObservedMessage[]> => {
  const bytes = await toUint8Array(data);
  if (!bytes) return [];
  const publish = decodeMqttPublish(bytes);
  if (!publish || publish.topic !== "chat") return [];
  try {
    const protocol = bossChatProtocol.decode(publish.payload);
    const normalized = await Promise.all(protocol.messages.map(normalizeMessage));
    return normalized.filter((item): item is ChatObservedMessage => item !== null);
  } catch {
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
    if (!isEnabled()) return;
    void decodeObservedChatFrame(event.data).then((messages) => Promise.all(
      messages.map((message) => onMessage(message))
    ));
  });
};

export const installBossChatObserver = (
  onMessage: (message: ChatObservedMessage) => Promise<void>
): { setEnabled(enabled: boolean): void; uninstall(): void } => {
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
      void decodeObservedChatFrame(data).then((messages) => Promise.all(
        messages.map((message) => onMessage(message))
      ));
    }
    return nativeSend.call(this, data);
  };
  window.WebSocket = ObservedWebSocket;

  return {
    setEnabled(value: boolean) { enabled = value; },
    uninstall() {
      window.WebSocket = NativeWebSocket;
      NativeWebSocket.prototype.send = nativeSend;
    }
  };
};
