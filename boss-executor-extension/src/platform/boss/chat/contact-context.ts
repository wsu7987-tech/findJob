type ContactContext = {
  peerUid: string;
  encryptPeerUid: string;
  securityId: string;
  encryptJobId: string;
  jobTitle: string;
  peerName: string;
  companyName: string;
};

type CachedContactContext = { value: ContactContext; expiresAt: number };

const cache = new Map<string, CachedContactContext>();
const CACHE_TTL_MS = 5 * 60_000;
const CACHE_MAX_ITEMS = 200;

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" ? value as Record<string, unknown> : {};

const text = (value: unknown): string => value == null ? "" : String(value);

export const resolveBossContactContext = async (
  accountUid: string,
  peerUid: string,
  encryptJobId = ""
): Promise<ContactContext> => {
  const now = Date.now();
  for (const [key, item] of cache) {
    if (item.expiresAt <= now) cache.delete(key);
  }
  const cacheKey = `${accountUid}:${peerUid}:${encryptJobId}`;
  const cached = cache.get(cacheKey);
  if (cached) return cached.value;
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
    cache.set(cacheKey, { value: result, expiresAt: now + CACHE_TTL_MS });
    if (cache.size > CACHE_MAX_ITEMS) {
      const oldestKey = cache.keys().next().value as string | undefined;
      if (oldestKey) cache.delete(oldestKey);
    }
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
