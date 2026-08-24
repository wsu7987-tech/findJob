type ContactContext = {
  peerUid: string;
  encryptPeerUid: string;
  securityId: string;
  encryptJobId: string;
  jobTitle: string;
  peerName: string;
  companyName: string;
};

const cache = new Map<string, ContactContext>();

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? value as Record<string, unknown> : {};

const text = (value: unknown): string => value == null ? "" : String(value);

export const resolveBossContactContext = async (peerUid: string): Promise<ContactContext> => {
  const cached = cache.get(peerUid);
  if (cached) return cached;
  try {
    // 只查询当前消息对端，不同步聊天历史。
    const response = await fetch(
      `https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json?friendIds=${encodeURIComponent(peerUid)}`,
      { credentials: "include" }
    );
    const body = record(await response.json());
    const data = record(body.zpData);
    const first = Array.isArray(data.result) ? record(data.result[0]) : {};
    const result: ContactContext = {
      peerUid,
      encryptPeerUid: text(first.encryptBossId),
      securityId: text(first.securityId),
      encryptJobId: text(first.encryptJobId),
      jobTitle: [first.brandName, first.title].map(text).filter(Boolean).join("-") ,
      peerName: text(first.name),
      companyName: text(first.brandName)
    };
    cache.set(peerUid, result);
    return result;
  } catch {
    return {
      peerUid,
      encryptPeerUid: "",
      securityId: "",
      encryptJobId: "",
      jobTitle: "",
      peerName: "",
      companyName: ""
    };
  }
};
